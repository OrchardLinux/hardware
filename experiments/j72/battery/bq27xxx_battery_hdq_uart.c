// SPDX-License-Identifier: GPL-2.0
/*
 * TI HDQ battery gauge over Apple UART5, bit-banged via serdev
 *
 * Derived from mattonik/ipad-nixos commit b94d42f78e04c5465e290340a5b6879bbc5956b6.
 * J72 adaptation: AP GPIO44 inherited function1, verified DEVICE_TYPE0545.
 * No PMU GPIO switching. Writes are limited to volatile DEVICE_TYPE query.
 *
 * J81 (iPad Air 2 Wi-Fi) wires its BQ2754x-family gas gauge to a single
 * AP GPIO pin (AP GPIO34) shared as UART5 TX, encoding TI's single-wire
 * HDQ protocol as UART bytes rather than exposing a real HDQ/1-Wire
 * master. See docs/plans/2026-09-08-j81-bluetooth-battery-adt.md and
 * research/j81-battery-hdq.md for the wiring evidence and protocol
 * analysis this driver implements. drivers/power/supply/bq27xxx_battery_hdq.c
 * is a different transport (a real Linux 1-Wire master) and does not
 * apply here.
 *
 * Wire encoding (matches the behavior documented for this exact wiring):
 *   - one HDQ bit = one UART byte: 0xfe = HDQ one, 0xc0 = HDQ zero;
 *   - one HDQ byte = eight UART bytes, sent/received LSB first;
 *   - the bus is a single wire, so our own transmitted bytes loop back
 *     on RX before the gauge's response bytes do -- every transaction
 *     reads and discards that echo first;
 *   - each transaction opens with an HDQ break (a low pulse) which,
 *     unlike a normal UART break, needs microsecond-precision timing
 *     that the tty layer's own break_ctl() does not provide, so this
 *     driver asserts/times/deasserts it manually instead of trusting
 *     a fixed-duration break request.
 */

#include <linux/completion.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/kernel.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/power/bq27xxx_battery.h>
#include <linux/serdev.h>
#include <linux/spinlock.h>
#include <linux/string.h>

#define HDQ_CMD_READ			(0 << 7)
#define HDQ_CMD_WRITE			(1 << 7)
#define HDQ_CMD_REG_MASK		0x7f

#define HDQ_UART_BAUD			57600
#define HDQ_BREAK_US_MIN		250
#define HDQ_BREAK_US_MAX		500
#define HDQ_RECOVERY_US_MIN		150
#define HDQ_RECOVERY_US_MAX		500
#define HDQ_XFER_TIMEOUT_MS		500
#define HDQ_WORD_READ_RETRIES		3

#define HDQ_UART_ONE			0xfe
#define HDQ_UART_ZERO			0xc0
#define HDQ_UART_ONE_MIN		0xf0
#define HDQ_UART_BYTE_LEN		8

#define BQ27XXX_REG_CONTROL		0x00
#define BQ27XXX_SUBCMD_DEVICE_TYPE	0x0001

struct hdq_uart {
	struct serdev_device *serdev;
	struct bq27xxx_device_info di;

	struct mutex xfer_lock;	/* serializes whole HDQ transactions */

	/* Filled in by the (possibly IRQ-context) receive_buf callback;
	 * protected by rx_lock, not the sleeping xfer_lock above.
	 */
	spinlock_t rx_lock;
	struct completion rx_done;
	u8 rx_start[HDQ_UART_BYTE_LEN];
	u8 *rx_buf;
	int rx_want;
	int rx_count;
};

static void hdq_encode_byte(u8 byte, u8 *out)
{
	int i;

	for (i = 0; i < HDQ_UART_BYTE_LEN; i++)
		out[i] = (byte & (1 << i)) ? HDQ_UART_ONE : HDQ_UART_ZERO;
}

static int hdq_decode_byte(const u8 *in, u8 *byte)
{
	u8 val = 0;
	int i;

	/* Preserve the working reference's threshold decoder. UART sampling
	 * can yield non-canonical low symbols (for example0xc3); do not
	 * invent a stricter pulse-shape requirement for the kernel frontend.
	 */
	for (i = 0; i < HDQ_UART_BYTE_LEN; i++)
		if (in[i] >= HDQ_UART_ONE_MIN)
			val |= (1 << i);
	*byte = val;
	return 0;
}

static size_t hdq_uart_receive_buf(struct serdev_device *serdev,
				   const u8 *data, size_t count)
{
	struct hdq_uart *hdq = serdev_device_get_drvdata(serdev);
	unsigned long flags;
	size_t consumed = 0;

	spin_lock_irqsave(&hdq->rx_lock, flags);
	while (consumed < count) {
		if (!hdq->rx_want) {
			consumed = count;
			break;
		}

		if (hdq->rx_count < hdq->rx_want)
			hdq->rx_buf[hdq->rx_count++] = data[consumed];
		consumed++;

		/* Require a contiguous command echo, across callback boundaries.
		 * A mismatching byte must not leave an old partial prefix valid.
		 * Retain a seven-byte sliding window until the real echo arrives.
		 */
		if (hdq->rx_count == HDQ_UART_BYTE_LEN &&
		    memcmp(hdq->rx_buf, hdq->rx_start, HDQ_UART_BYTE_LEN)) {
			memmove(hdq->rx_buf, hdq->rx_buf + 1, HDQ_UART_BYTE_LEN - 1);
			hdq->rx_count--;
		}

		if (hdq->rx_count >= hdq->rx_want)
			complete(&hdq->rx_done);
	}
	spin_unlock_irqrestore(&hdq->rx_lock, flags);

	return consumed;
}

static const struct serdev_device_ops hdq_uart_serdev_ops = {
	.receive_buf = hdq_uart_receive_buf,
	.write_wakeup = serdev_device_write_wakeup,
};

static void hdq_arm_receive(struct hdq_uart *hdq, u8 *buf, int n,
			    const u8 *start)
{
	unsigned long flags;

	spin_lock_irqsave(&hdq->rx_lock, flags);
	memcpy(hdq->rx_start, start, HDQ_UART_BYTE_LEN);
	hdq->rx_buf = buf;
	hdq->rx_count = 0;
	hdq->rx_want = n;
	reinit_completion(&hdq->rx_done);
	spin_unlock_irqrestore(&hdq->rx_lock, flags);
}

static void hdq_disarm_receive(struct hdq_uart *hdq)
{
	unsigned long flags;

	spin_lock_irqsave(&hdq->rx_lock, flags);
	hdq->rx_buf = NULL;
	hdq->rx_want = 0;
	spin_unlock_irqrestore(&hdq->rx_lock, flags);
}

/* The receiver is armed before transmit so an immediate loopback echo or
 * gauge response cannot race ahead of the waiter. Echo and response use one
 * buffer and one completion because the tty layer may deliver both together.
 */
static int hdq_wait_receive(struct hdq_uart *hdq)
{
	long ret;

	ret = wait_for_completion_timeout(&hdq->rx_done,
					  msecs_to_jiffies(HDQ_XFER_TIMEOUT_MS));
	hdq_disarm_receive(hdq);

	return ret ? 0 : -ETIMEDOUT;
}

/* Asserts an HDQ break with the microsecond timing TI's spec requires,
 * followed by the recovery gap -- both far shorter than break_ctl()'s
 * own jiffies-granularity timing, so this drives it manually instead of
 * trusting a fixed-duration break request.
 */
static int hdq_send_break(struct hdq_uart *hdq)
{
	int ret;

	ret = serdev_device_break_ctl(hdq->serdev, -1);
	if (ret)
		return ret;
	usleep_range(HDQ_BREAK_US_MIN, HDQ_BREAK_US_MAX);
	ret = serdev_device_break_ctl(hdq->serdev, 0);
	if (ret)
		return ret;
	usleep_range(HDQ_RECOVERY_US_MIN, HDQ_RECOVERY_US_MAX);

	return 0;
}

/* One full HDQ byte transaction: break, send `n_tx` encoded UART bytes
 * (the command byte alone for a read, command+data for a write),
 * discard the single-wire echo of what we just sent, then -- for a
 * read only -- collect the gauge's own encoded response byte.
 */
static int hdq_transact(struct hdq_uart *hdq, const u8 *tx, int n_tx,
			u8 *resp)
{
	u8 rx[3 * HDQ_UART_BYTE_LEN];
	ssize_t written;
	int ret, rx_want = n_tx + (resp ? HDQ_UART_BYTE_LEN : 0);

	if (rx_want > ARRAY_SIZE(rx))
		return -EINVAL;

	ret = hdq_send_break(hdq);
	if (ret)
		return ret;

	hdq_arm_receive(hdq, rx, rx_want, tx);
	written = serdev_device_write(hdq->serdev, tx, n_tx,
				      msecs_to_jiffies(HDQ_XFER_TIMEOUT_MS));
	if (written != n_tx) {
		hdq_disarm_receive(hdq);
		return written < 0 ? (int)written : -EIO;
	}

	serdev_device_wait_until_sent(hdq->serdev,
				      msecs_to_jiffies(HDQ_XFER_TIMEOUT_MS));
	ret = hdq_wait_receive(hdq);
	if (ret) {
		unsigned long flags;
		int got;

		/* Before a complete command echo, this is the pending sliding
		 * window. Once the echo matches it includes echo+response.
		 */
		spin_lock_irqsave(&hdq->rx_lock, flags);
		got = hdq->rx_count;
		spin_unlock_irqrestore(&hdq->rx_lock, flags);

		dev_info(&hdq->serdev->dev,
			 "HDQ transact timeout: buffered %d/%d bytes: %*ph\n",
			 got, rx_want, got, rx);
		return ret;
	}
	if (memcmp(tx, rx, n_tx) != 0)
		return -EIO;

	if (resp)
		memcpy(resp, rx + n_tx, HDQ_UART_BYTE_LEN);

	return 0;
}

static int hdq_read_byte(struct hdq_uart *hdq, u8 reg, u8 *val)
{
	u8 cmd = HDQ_CMD_READ | (reg & HDQ_CMD_REG_MASK);
	u8 tx[HDQ_UART_BYTE_LEN], resp[HDQ_UART_BYTE_LEN];
	int ret;

	hdq_encode_byte(cmd, tx);

	mutex_lock(&hdq->xfer_lock);
	ret = hdq_transact(hdq, tx, sizeof(tx), resp);
	mutex_unlock(&hdq->xfer_lock);
	if (ret)
		return ret;

	return hdq_decode_byte(resp, val);
}

static int hdq_write_byte(struct hdq_uart *hdq, u8 reg, u8 val)
{
	u8 cmd = HDQ_CMD_WRITE | (reg & HDQ_CMD_REG_MASK);
	u8 tx[2 * HDQ_UART_BYTE_LEN];
	int ret;

	/* Sealed-safe volatile DEVICE_TYPE0001 only: no arbitrary writes. */
	if (!((reg == 0x00 && val == 0x01) ||
	      (reg == 0x01 && val == 0x00)))
		return -EACCES;
	hdq_encode_byte(cmd, tx);
	hdq_encode_byte(val, tx + HDQ_UART_BYTE_LEN);

	mutex_lock(&hdq->xfer_lock);
	ret = hdq_transact(hdq, tx, sizeof(tx), NULL);
	mutex_unlock(&hdq->xfer_lock);

	return ret;
}

static int hdq_uart_read(struct bq27xxx_device_info *di, u8 reg, bool single)
{
	struct hdq_uart *hdq = container_of(di, struct hdq_uart, di);
	unsigned int timeout = HDQ_WORD_READ_RETRIES;
	u8 lower, upper, prev_upper;
	int ret;

	if (single) {
		ret = hdq_read_byte(hdq, reg, &lower);
		return ret ? ret : lower;
	}

	/* Stable 16-bit read: high/low/high, retrying if the high byte
	 * changed between the two reads (the gauge's counters can tick
	 * mid-transaction).
	 */
	ret = hdq_read_byte(hdq, reg + 1, &upper);
	if (ret)
		return ret;

	do {
		prev_upper = upper;

		ret = hdq_read_byte(hdq, reg, &lower);
		if (ret)
			return ret;

		ret = hdq_read_byte(hdq, reg + 1, &upper);
		if (ret)
			return ret;
	} while (prev_upper != upper && --timeout);

	if (timeout == 0)
		return -EIO;

	return (upper << 8) | lower;
}

static int hdq_uart_write(struct bq27xxx_device_info *di, u8 reg, int value,
			  bool single)
{
	struct hdq_uart *hdq = container_of(di, struct hdq_uart, di);
	int ret;

	ret = hdq_write_byte(hdq, reg, value & 0xff);
	if (ret || single)
		return ret;

	return hdq_write_byte(hdq, reg + 1, (value >> 8) & 0xff);
}

/* TI's sealed-access-safe Control() DEVICE_TYPE subcommand: write 0x0001
 * to Control() (register 0x00/0x01), then read the same register pair
 * back for the chip's numeric device type. Documented values include
 * 0x0541 (BQ27541 family) and 0x0545 (BQ27545 family); the ADT's
 * "bq27540" compatible string is a family hint, not a specific chip
 * revision, so the real behavior is decided by this readback, not the
 * string. An unrecognized value fails probe rather than guessing a
 * register layout that may not match.
 */
static int hdq_uart_identify(struct hdq_uart *hdq, struct device *dev)
{
	u16 device_type;
	int ret;

	ret = hdq_uart_write(&hdq->di, BQ27XXX_REG_CONTROL,
			     BQ27XXX_SUBCMD_DEVICE_TYPE, false);
	if (ret)
		return ret;

	ret = hdq_uart_read(&hdq->di, BQ27XXX_REG_CONTROL, false);
	if (ret < 0)
		return ret;

	device_type = ret & 0xffff;
	dev_info(dev, "HDQ DEVICE_TYPE = 0x%04x\n", device_type);

	if (device_type != 0x0545) {
		dev_err(dev, "J72 expected verified DEVICE_TYPE0545, got %04x\n", device_type);
		return -ENODEV;
	}
	hdq->di.chip = BQ27545;
	hdq->di.name = "j72-bq27545-battery";

	return 0;
}

static int hdq_uart_probe(struct serdev_device *serdev)
{
	struct device *dev = &serdev->dev;
	struct hdq_uart *hdq;
	int ret;

	if (!of_machine_is_compatible("apple,j72"))
		return -ENODEV;

	hdq = devm_kzalloc(dev, sizeof(*hdq), GFP_KERNEL);
	if (!hdq)
		return -ENOMEM;

	hdq->serdev = serdev;
	mutex_init(&hdq->xfer_lock);
	spin_lock_init(&hdq->rx_lock);
	init_completion(&hdq->rx_done);

	serdev_device_set_drvdata(serdev, hdq);
	serdev_device_set_client_ops(serdev, &hdq_uart_serdev_ops);

	ret = devm_serdev_device_open(dev, serdev);
	if (ret)
		return ret;

	if (serdev_device_set_baudrate(serdev, HDQ_UART_BAUD) != HDQ_UART_BAUD)
		return dev_err_probe(dev, -ERANGE, "failed to select 57600 baud\n");
	serdev_device_set_flow_control(serdev, false);
	ret = serdev_device_set_parity(serdev, SERDEV_PARITY_NONE);
	if (ret)
		return dev_err_probe(dev, ret, "failed to select no parity\n");

	/* BAT-1: the reason this driver needs the new serdev stop-bit
	 * helper at all -- TI's HDQ timing only fits inside 8N2 framing on
	 * this UART's real bit rate (see research/j81-battery-hdq.md's
	 * timing table); 8N1 leaves too short a cycle.
	 */
	ret = serdev_device_set_stopbits(serdev, SERDEV_STOPBITS_TWO);
	if (ret)
		return dev_err_probe(dev, ret, "failed to select two stop bits\n");

	hdq->di.dev = dev;
	hdq->di.bus.read = hdq_uart_read;
	hdq->di.bus.write = hdq_uart_write;

	ret = hdq_uart_identify(hdq, dev);
	if (ret)
		return dev_err_probe(dev, ret, "HDQ device identification failed\n");

	return bq27xxx_battery_setup(&hdq->di);
}

static void hdq_uart_remove(struct serdev_device *serdev)
{
	struct hdq_uart *hdq = serdev_device_get_drvdata(serdev);

	bq27xxx_battery_teardown(&hdq->di);
}

static const struct of_device_id hdq_uart_of_match[] = {
	/* Provisional development compatible tied to this board's ADT
	 * evidence (docs/plans/2026-09-08-j81-bluetooth-battery-adt.md) --
	 * to be finalized against the real DEVICE_TYPE readback logged
	 * above before this driver is considered upstreamable.
	 */
	{ .compatible = "ti,bq27540-hdq-uart" },
	{ }
};
MODULE_DEVICE_TABLE(of, hdq_uart_of_match);

static struct serdev_device_driver hdq_uart_driver = {
	.probe = hdq_uart_probe,
	.remove = hdq_uart_remove,
	.driver = {
		.name = "bq27xxx-hdq-uart",
		.of_match_table = hdq_uart_of_match,
	},
};
module_serdev_device_driver(hdq_uart_driver);

MODULE_DESCRIPTION("TI HDQ battery gauge over UART (single-wire, bit-banged via serdev)");
MODULE_LICENSE("GPL");

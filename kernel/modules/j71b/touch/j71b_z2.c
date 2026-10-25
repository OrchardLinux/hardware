// SPDX-License-Identifier: GPL-2.0
/*
 * Apple Z2 touchscreen driver
 *
 * Copyright (C) The Asahi Linux Contributors
 */

/* J71b HBPP startup uses cached firmware and a 17-byte DMA threshold.
 * No sync-pin writes; one ready event plus DeviceInfo gates input registration.
 */
#include <linux/atomic.h>
#include <linux/bitrev.h>
#include <linux/bits.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/gpio/consumer.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include <linux/input/touchscreen.h>
#include <linux/interrupt.h>
#include <linux/irq.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/pinctrl/consumer.h>
#include <linux/regulator/consumer.h>
#include <linux/spi/spi.h>
#include <linux/unaligned.h>
#include "quiesce-api.h"
#include "fw-plan.h"
#include "verified-bootstrap.h"

#define APPLE_Z2_NUM_FINGERS_OFFSET      16
#define APPLE_Z2_FINGERS_OFFSET          24
#define APPLE_Z2_TOUCH_STARTED           3
#define APPLE_Z2_TOUCH_MOVED             4
#define APPLE_Z2_CMD_READ_INTERRUPT_DATA 0xEB
#define APPLE_Z2_REPLY_INTERRUPT_DATA    0xE1
#define APPLE_Z2_FRAME_INPUT_REPORT      0x90
#define APPLE_Z2_INTERFACE_GRAPE         BIT(1)
#define APPLE_Z2_HID_REPORT_ID           0x76
#define APPLE_Z2_D111_TOUCH_REPORT       0x44
#define APPLE_Z2_D11_STATUS_REPORT       0x50
#define APPLE_Z2_REPORT_ENABLE           0xaf
#define APPLE_Z2_D11_REPORT_ENABLE       0xe2
#define APPLE_Z2_J172_HEADER_SIZE         32
#define APPLE_Z2_J172_CONTACT_SIZE        48
#define APPLE_Z2_J172_MAX_CONTACTS        32
#define APPLE_Z2_J172_FLOAT_FORMAT        BIT(1)
#define APPLE_Z2_REPORT_SURFACE           0xd9
#define APPLE_Z2_SURFACE_DESCRIPTOR_SIZE  16
#define APPLE_Z2_CMD_DEVICE_INFO         0xE2
#define APPLE_Z2_CMD_REPORT_INFO         0xE3
#define APPLE_Z2_CMD_CTRL_WRITE_SHORT    0xE4
#define APPLE_Z2_CMD_CTRL_READ_SHORT     0xE6
#define APPLE_Z2_CMD_CTRL_READ_LONG      0xE7
#define APPLE_Z2_CMD_LAST                0xE1
#define APPLE_Z2_CMD_WAKE                0xEE
#define APPLE_Z2_CMD_SIZE                16
#define APPLE_Z2_RAW_XFER_MAX_SIZE       64
#define APPLE_Z2_GEN2_MIN_RESULT_SIZE    64
#define APPLE_Z2_HBPP_CMD_BLOB           0x3001
#define APPLE_Z2_FW_MAGIC                0x5746325A
#define APPLE_Z2_RX_BUF_SIZE             8192
#define APPLE_Z2_D111_CAL_SIZE           0x400
#define LOAD_COMMAND_INIT_PAYLOAD        0
#define LOAD_COMMAND_SEND_BLOB           1
#define LOAD_COMMAND_SEND_CALIBRATION    2
#define LOAD_COMMAND_WAIT_IRQ            3
#define LOAD_COMMAND_SET_CONFIG          4
#define LOAD_COMMAND_RAW_XFER            5
#define LOAD_COMMAND_SEND_CALIBRATION_V2 6
#define APPLE_Z2_RAW_XFER_RX_BIT_REVERSE BIT(0)
#define CAL_PROP_NAME                    "apple,z2-cal-blob"
#define ORB_GAP_CAL_PROP_NAME            "apple,z2-orb-gap-cal-blob"
#define ORB_FORCE_CAL_PROP_NAME          "apple,z2-orb-force-cal-blob"
#define SHAPE_ACCEL_CAL_PROP_NAME        "apple,z2-shape-accel-cal-blob"

#define APPLE_Z2_FW_CONFIG_WORDS         11
#define APPLE_Z2_FW_CONFIG_MIN_DMA       BIT(0)
#define APPLE_Z2_FW_CONFIG_Z2_DELAY      BIT(1)
#define APPLE_Z2_FW_CONFIG_CS_DELAY      BIT(2)
#define APPLE_Z2_FW_CONFIG_CPHA          BIT(3)
#define APPLE_Z2_FW_CONFIG_CPOL          BIT(4)
#define APPLE_Z2_FW_CONFIG_BOOT_TIMEOUT  BIT(8)

enum apple_z2_protocol_state {
	APPLE_Z2_STATE_OFF,
	APPLE_Z2_STATE_POWERED,
	APPLE_Z2_STATE_SPI_CONFIGURED,
	APPLE_Z2_STATE_BOOT_IRQ,
	APPLE_Z2_STATE_HBPP_READY,
	APPLE_Z2_STATE_FIRMWARE_READY,
	APPLE_Z2_STATE_DEVICE_INFO,
	APPLE_Z2_STATE_RUNTIME,
	APPLE_Z2_STATE_REPORTS_READY,
};

enum apple_z2_variant {
	APPLE_Z2_VARIANT_GENERIC,
	APPLE_Z2_VARIANT_J172,
	APPLE_Z2_VARIANT_J71B,
	APPLE_Z2_VARIANT_D11,
	APPLE_Z2_VARIANT_D111,
};

static bool enable_runtime;
module_param(enable_runtime,bool,0444);
static int result=-EINPROGRESS;
module_param(result,int,0444);
static atomic_t attempted=ATOMIC_INIT(0);
static unsigned int runtime_irqs,runtime_packets,runtime_empty,runtime_bad,runtime_controls,runtime_no_data;
module_param(runtime_irqs,uint,0444);
module_param(runtime_packets,uint,0444);
module_param(runtime_empty,uint,0444);
module_param(runtime_bad,uint,0444);
module_param(runtime_controls,uint,0444);
module_param(runtime_no_data,uint,0444);

struct apple_z2_chip_info {
	const char *name;
	enum apple_z2_variant variant;
};

struct apple_z2 {
	struct spi_device *spidev;
	struct gpio_desc *reset_gpio;
	struct gpio_desc *power_ana_gpio;
	struct gpio_desc *power_ldo_gpio;
	struct gpio_desc *display_sync_gpio;
	struct gpio_desc *display_sync1_gpio;
	struct pinctrl *j172_sync_pinctrl;
	struct pinctrl_state *j172_sync_active_state;
	struct regulator *hv_supply;
	struct regulator *core_supply;
	struct clk *clk;
	struct input_dev *input_dev;
	struct completion boot_irq;
	struct mutex io_lock; /* Serializes command and IRQ transfers. */
	enum apple_z2_variant variant;
	bool no_init_ack;
	bool booted;
	bool clk_enabled;
	bool platform_powered;
	bool upload_irq_masked;
	bool runtime_frame_valid;
	bool runtime_queue_empty;
	bool runtime_control_reply;
	bool irq_type_changed;
	unsigned int saved_irq_type;
	bool surface_descriptor_valid;
	bool j172_sync_pinctrl_active;
	bool hv_enabled;
	bool core_enabled;
	enum apple_z2_protocol_state protocol_state;
	unsigned int bpw16_min_len;
	unsigned int dma_min_len;
	unsigned int z2_inter_packet_delay_us;
	unsigned int z2_cs_delay_us;
	unsigned int boot_timeout_ms;
	s16 sensor_max_x;
	s16 sensor_min_x;
	s16 sensor_max_y;
	s16 sensor_min_y;
	int index_parity;
	struct touchscreen_properties props;
	const char *fw_name;
	const struct firmware *validated_fw;
	struct j71b_readiness verified_boot;
	u8 *tx_buf;
	u8 *rx_buf;
};

static bool apple_z2_uses_ipad_protocol(const struct apple_z2 *z2)
{
	return z2->variant == APPLE_Z2_VARIANT_J172 || z2->variant == APPLE_Z2_VARIANT_J71B;
}
static bool apple_z2_is_j71b(const struct apple_z2 *z2)
{ return z2->variant == APPLE_Z2_VARIANT_J71B; }

static bool apple_z2_is_d11(const struct apple_z2 *z2)
{
	return z2->variant == APPLE_Z2_VARIANT_D11;
}

static bool apple_z2_is_d111(const struct apple_z2 *z2)
{
	return z2->variant == APPLE_Z2_VARIANT_D111;
}

static bool apple_z2_is_iphone7_plus(const struct apple_z2 *z2)
{
	return apple_z2_is_d11(z2) || apple_z2_is_d111(z2);
}

struct apple_z2_finger {
	u8 finger;
	u8 state;
	__le16 unknown2;
	__le16 abs_x;
	__le16 abs_y;
	__le16 rel_x;
	__le16 rel_y;
	__le16 tool_major;
	__le16 tool_minor;
	__le16 orientation;
	__le16 touch_major;
	__le16 touch_minor;
	__le16 unused[2];
	__le16 pressure;
	__le16 multi;
} __packed;

struct apple_z2_hbpp_blob_hdr {
	__le16 cmd;
	__le16 len;
	__le32 addr;
	__le16 checksum;
};

struct apple_z2_fw_hdr {
	__le32 magic;
	__le32 version;
};

struct apple_z2_fw_calibration {
	__le32 address;
	__le32 max_size;
	__le32 provider;
};

enum apple_z2_calibration_provider {
	APPLE_Z2_CAL_MULTI_TOUCH,
	APPLE_Z2_CAL_ORB_GAP,
	APPLE_Z2_CAL_ORB_FORCE,
	APPLE_Z2_CAL_SHAPE_ACCEL,
};

static void apple_z2_apply_z2_delays(struct apple_z2 *z2);
static void apple_z2_post_z2_xfer_delay(struct apple_z2 *z2);
static void apple_z2_set_gpio(struct gpio_desc *gpio, int value);
static void apple_z2_z2_checksum(u8 *cmd, unsigned int len);
static bool apple_z2_z2_checksum_valid(const u8 *cmd,unsigned int len);

static bool apple_z2_float_to_integer(const u8 *raw, s64 *value)
{
	u32 bits = get_unaligned_le32(raw);
	u32 exponent = (bits >> 23) & 0xff;
	u64 significand;
	u64 magnitude;
	int shift;

	if (exponent == 0xff)
		return false;
	if (!exponent) {
		*value = 0;
		return true;
	}

	significand = BIT_ULL(23) | (bits & GENMASK(22, 0));
	shift = exponent - 127 - 23;
	if (shift >= 0) {
		if (shift > 7)
			return false;
		magnitude = significand << shift;
	} else if (-shift >= 64) {
		magnitude = 0;
	} else {
		magnitude = DIV_ROUND_CLOSEST_ULL(significand,
						  BIT_ULL(-shift));
	}

	if (magnitude > S32_MAX)
		return false;
	*value = bits & BIT(31) ? -(s64)magnitude : magnitude;
	return true;
}

static bool apple_z2_scale_j172_coord(const u8 *raw, s16 maximum,
				      s16 minimum, unsigned int pixels,
				      unsigned int *position)
{
	s64 coordinate;
	s64 numerator;
	s64 denominator;

	if (maximum <= minimum || !apple_z2_float_to_integer(raw, &coordinate))
		return false;

	/* The report uses micrometres; the descriptor uses 0.01 mm. */
	numerator = coordinate - 10LL * minimum;
	denominator = 10LL * (maximum - minimum);
	if (numerator <= 0) {
		*position = 0;
	} else if (numerator >= denominator) {
		*position = pixels;
	} else {
		*position = DIV_ROUND_CLOSEST_ULL((u64)numerator * pixels,
						  denominator);
	}

	return true;
}

static bool apple_z2_scale_iphone7_coord(s16 coordinate, s16 maximum,
					 s16 minimum, unsigned int pixels,
					 unsigned int *position)
{
	s64 numerator;
	s64 denominator;

	if (maximum <= minimum)
		return false;

	numerator = coordinate - minimum;
	denominator = maximum - minimum;
	if (numerator <= 0)
		*position = 0;
	else if (numerator >= denominator)
		*position = pixels;
	else
		*position = DIV_ROUND_CLOSEST_ULL((u64)numerator * pixels,
						  denominator);

	return true;
}

static void apple_z2_parse_j172_touches(struct apple_z2 *z2,
					const u8 *msg, size_t msg_len)
{
	unsigned int contact_offset;
	unsigned int contact_bytes;
	unsigned int nfingers;
	unsigned int x;
	unsigned int y;
	unsigned int i;
	const u8 *contact;
	int slot;
	bool active;
	bool coords_valid;

	if (msg_len < APPLE_Z2_J172_HEADER_SIZE)
		return;

	contact_offset = msg[2] + msg[14];
	contact_bytes = get_unaligned_le16(msg + 16);
	nfingers = msg[22];
	if (msg[2] < APPLE_Z2_J172_HEADER_SIZE ||
	    !(get_unaligned_le16(msg + 12) & APPLE_Z2_J172_FLOAT_FORMAT) ||
	    nfingers > APPLE_Z2_J172_MAX_CONTACTS ||
	    contact_bytes != nfingers * APPLE_Z2_J172_CONTACT_SIZE ||
	    contact_offset > msg_len ||
	    contact_bytes > msg_len - contact_offset) {
		dev_warn_ratelimited(&z2->spidev->dev,
				     "invalid J172 touch packet layout len=%zu header=%u contacts=%u bytes=%u\n",
				     msg_len, msg[2], nfingers, contact_bytes);
		return;
	}

	if (nfingers && !z2->surface_descriptor_valid) {
		dev_warn_ratelimited(&z2->spidev->dev,
				     "J172 touch packet without surface descriptor\n");
		return;
	}

	contact = msg + contact_offset;
	for (i = 0; i < nfingers; i++, contact += APPLE_Z2_J172_CONTACT_SIZE) {
		slot = input_mt_get_slot_by_key(z2->input_dev, contact[0]);
		if (slot < 0) {
			dev_warn(&z2->spidev->dev,
				 "unable to get slot for J172 finger\n");
			continue;
		}

		active = contact[1] == APPLE_Z2_TOUCH_STARTED ||
			 contact[1] == APPLE_Z2_TOUCH_MOVED;
		input_mt_slot(z2->input_dev, slot);
		if (!input_mt_report_slot_state(z2->input_dev, MT_TOOL_FINGER, active))
			continue;

		coords_valid = apple_z2_scale_j172_coord(contact + 4,
							 z2->sensor_max_x,
							 z2->sensor_min_x,
							 z2->props.max_x, &x);
		if (coords_valid)
			coords_valid = apple_z2_scale_j172_coord(contact + 8,
								 z2->sensor_max_y,
								 z2->sensor_min_y,
								 z2->props.max_y, &y);
		if (!coords_valid) {
			dev_warn_ratelimited(&z2->spidev->dev,
					     "invalid J172 touch coordinates\n");
			continue;
		}

		touchscreen_report_pos(z2->input_dev, &z2->props, x,
				       z2->props.max_y - y, true);
	}

	input_mt_sync_frame(z2->input_dev);
	input_sync(z2->input_dev);
}

static void apple_z2_parse_touches(struct apple_z2 *z2,
				   const u8 *msg, size_t msg_len)
{
	s16 abs_x;
	s16 abs_y;
	s16 orientation;
	bool coords_valid;
	unsigned int x;
	unsigned int y;
	int i;
	int nfingers;
	int slot;
	int slot_valid;
	struct apple_z2_finger *fingers;

	if (msg_len >= APPLE_Z2_J172_HEADER_SIZE &&
	    msg[0] == APPLE_Z2_HID_REPORT_ID && msg[3] == 4) {
		apple_z2_parse_j172_touches(z2, msg, msg_len);
		return;
	}

	if (apple_z2_is_j71b(z2)) {
		dev_warn_ratelimited(&z2->spidev->dev,"J71B_RUNTIME_UNKNOWN_CONTACT_LAYOUT len=%zu header=%*ph\n",msg_len,(int)min_t(size_t,msg_len,32),msg);
		return;
	}

	if (msg_len <= APPLE_Z2_NUM_FINGERS_OFFSET)
		return;
	nfingers = msg[APPLE_Z2_NUM_FINGERS_OFFSET];
	if (msg_len < APPLE_Z2_FINGERS_OFFSET ||
	    nfingers > (msg_len - APPLE_Z2_FINGERS_OFFSET) /
		       sizeof(*fingers)) {
		dev_warn(&z2->spidev->dev,
			 "invalid touch packet length %zu for %d fingers\n",
			 msg_len, nfingers);
		return;
	}
	if (apple_z2_is_iphone7_plus(z2) && nfingers &&
	    !z2->surface_descriptor_valid) {
		dev_warn_ratelimited(&z2->spidev->dev,
				     "iPhone 7 Plus touch packet without surface descriptor\n");
		return;
	}
	fingers = (struct apple_z2_finger *)(msg + APPLE_Z2_FINGERS_OFFSET);
	for (i = 0; i < nfingers; i++) {
		slot = input_mt_get_slot_by_key(z2->input_dev, fingers[i].finger);
		if (slot < 0) {
			dev_warn(&z2->spidev->dev, "unable to get slot for finger\n");
			continue;
		}
		slot_valid = fingers[i].state == APPLE_Z2_TOUCH_STARTED ||
			     fingers[i].state == APPLE_Z2_TOUCH_MOVED;
		input_mt_slot(z2->input_dev, slot);
		if (!input_mt_report_slot_state(z2->input_dev, MT_TOOL_FINGER, slot_valid))
			continue;
		if (apple_z2_is_iphone7_plus(z2)) {
			abs_x = (s16)le16_to_cpu(fingers[i].abs_x);
			abs_y = (s16)le16_to_cpu(fingers[i].abs_y);
			coords_valid = apple_z2_scale_iphone7_coord(abs_x,
								    z2->sensor_max_x,
								    z2->sensor_min_x,
								    z2->props.max_x, &x);
			if (coords_valid)
				coords_valid = apple_z2_scale_iphone7_coord(abs_y,
									    z2->sensor_max_y,
									    z2->sensor_min_y,
									    z2->props.max_y,
									    &y);
			if (!coords_valid) {
				dev_warn_ratelimited(&z2->spidev->dev,
						     "invalid iPhone 7 Plus touch coordinates\n");
				continue;
			}
			touchscreen_report_pos(z2->input_dev, &z2->props, x,
					       z2->props.max_y - y,
					       true);
		} else {
			touchscreen_report_pos(z2->input_dev, &z2->props,
					       le16_to_cpu(fingers[i].abs_x),
					       le16_to_cpu(fingers[i].abs_y),
					       true);
		}
		input_report_abs(z2->input_dev, ABS_MT_WIDTH_MAJOR,
				 le16_to_cpu(fingers[i].tool_major));
		input_report_abs(z2->input_dev, ABS_MT_WIDTH_MINOR,
				 le16_to_cpu(fingers[i].tool_minor));
		orientation = (s16)le16_to_cpu(fingers[i].orientation);
		if (apple_z2_is_iphone7_plus(z2))
			orientation = (s16)(0x4000 - orientation);
		input_report_abs(z2->input_dev, ABS_MT_ORIENTATION, orientation);
		input_report_abs(z2->input_dev, ABS_MT_TOUCH_MAJOR,
				 le16_to_cpu(fingers[i].touch_major));
		input_report_abs(z2->input_dev, ABS_MT_TOUCH_MINOR,
				 le16_to_cpu(fingers[i].touch_minor));
	}
	input_mt_sync_frame(z2->input_dev);
	input_sync(z2->input_dev);
}

static void apple_z2_dispatch_frame(struct apple_z2 *z2, const u8 *payload,
				    size_t payload_len)
{
	const u8 *report;
	u16 report_len;

	/* iPhone 7 Plus Gen2 packets carry the legacy report header directly. */
	if (apple_z2_is_iphone7_plus(z2)) {
		if (payload_len < APPLE_Z2_FINGERS_OFFSET)
			return;

		/*
		 * D111 hardware traces identify 0x44 as its touch report.  No D11
		 * runtime capture currently establishes a report identifier, so
		 * retain its length-based dispatch until that protocol is verified.
		 */
		if (apple_z2_is_d111(z2) &&
		    payload[0] != APPLE_Z2_D111_TOUCH_REPORT)
			return;

		apple_z2_parse_touches(z2, payload, payload_len);
		return;
	}

	if (payload_len < 4 || payload[0] != APPLE_Z2_FRAME_INPUT_REPORT)
		return;

	report_len = get_unaligned_le16(payload + 2);
	if (report_len != payload_len - 4) {
		dev_warn_ratelimited(&z2->spidev->dev,
				     "invalid input report length %u for payload %zu\n",
				     report_len, payload_len);
		return;
	}

	if (!(payload[1] & APPLE_Z2_INTERFACE_GRAPE) || !report_len)
		return;

	report = payload + 4;
	if (report[0] != APPLE_Z2_HID_REPORT_ID)
		return;

	apple_z2_parse_touches(z2, report, report_len);
}

static bool apple_z2_gen2_packet_valid(const u8 *buf, size_t buf_len,
				       u8 counter, bool strict_counter)
{
	bool counter_valid;
	u16 payload_len;
	u16 checksum;
	int i;

	if (buf_len < 7)
		return false;

	counter_valid = strict_counter ? buf[1] == counter :
					buf[1] == 1 || buf[1] == 2;
	payload_len = get_unaligned_le16(buf + 2);
	if (((buf[0] + buf[1] + buf[2] + buf[3] + buf[4]) & 0xff) ||
	    (buf[0] & 0xfe) != 0xea || !counter_valid ||
	    payload_len < 2 || payload_len + 5 > buf_len)
		return false;

	checksum = get_unaligned_le16(buf + 5 + payload_len - 2);
	for (i = 0; i < payload_len - 2; i++)
		checksum -= buf[5 + i];

	return !checksum;
}

/* Sandcastle's kernel keeps TX and RX storage separate. In particular, FIFO
 * receive processing must not overwrite bytes still awaiting transmit refill.
 */
static int j71b_exchange_separate(struct apple_z2 *z2,u8 *receive,size_t length)
{
 u8 *transmit;
 struct spi_transfer xfer={.rx_buf=receive,.len=length,.bits_per_word=8,.speed_hz=500000};
 u32 saved_mode=z2->spidev->mode;
 bool changed=length>16;
 int ret,restore_ret;
 if(!length || length>APPLE_Z2_RX_BUF_SIZE)return -EMSGSIZE;
 transmit=kmemdup(receive,length,GFP_KERNEL);
 if(!transmit)return -ENOMEM;
 xfer.tx_buf=transmit;
 if(changed){
  z2->spidev->mode=saved_mode&~SPI_CPHA;
  ret=spi_setup(z2->spidev);
  if(ret)goto restore_mode;
 }
 ret=spi_sync_transfer(z2->spidev,&xfer,1);
 restore_mode:
 if(changed){
  z2->spidev->mode=saved_mode;
  restore_ret=spi_setup(z2->spidev);
  if(!ret)ret=restore_ret;
 }
 kfree(transmit);
 return ret;
}

static int apple_z2_read_packet(struct apple_z2 *z2)
{
	struct spi_transfer xfer = { };
	bool strict_counter;
	bool header_empty=false;
	int error;
	size_t pkt_len;
	size_t wire_len;
	u16 payload_len;
	u8 counter;

	z2->runtime_frame_valid = false;
	z2->runtime_queue_empty = false;
	z2->runtime_control_reply = false;
	memset(z2->tx_buf, 0, APPLE_Z2_CMD_SIZE);
	memset(z2->rx_buf, 0, APPLE_Z2_CMD_SIZE);
	z2->tx_buf[0] = APPLE_Z2_CMD_READ_INTERRUPT_DATA;
	counter = z2->index_parity + 1;
	z2->tx_buf[1] = counter;
	apple_z2_z2_checksum(z2->tx_buf, APPLE_Z2_CMD_SIZE);
	xfer.tx_buf = z2->tx_buf;
	xfer.rx_buf = z2->rx_buf;
	xfer.len = APPLE_Z2_CMD_SIZE;

	error = spi_sync_transfer(z2->spidev, &xfer, 1);
	if (error)
		return error;
	apple_z2_post_z2_xfer_delay(z2);

	if (z2->rx_buf[0] != APPLE_Z2_REPLY_INTERRUPT_DATA) {
		if (apple_z2_is_j71b(z2) && z2->protocol_state < APPLE_Z2_STATE_RUNTIME)
			dev_info(&z2->spidev->dev,"J71B_RUNTIME_INITIAL_HEADER %*ph\n",16,z2->rx_buf);
		return 0;
	}

	payload_len=get_unaligned_le16(z2->rx_buf+1);
	header_empty=apple_z2_is_j71b(z2) && payload_len==0;
	if (apple_z2_is_j71b(z2) && z2->protocol_state < APPLE_Z2_STATE_RUNTIME)
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_QUEUE_HEADER length=%u bytes=%*ph\n",payload_len,16,z2->rx_buf);
	pkt_len = apple_z2_is_j71b(z2) ? max_t(size_t,16,(size_t)payload_len+5) :
		  (payload_len + 8) & 0xfffffffc;
	if (pkt_len > APPLE_Z2_RX_BUF_SIZE) {
		dev_warn(&z2->spidev->dev, "packet too large: %zu\n", pkt_len);
		return -EMSGSIZE;
	}
	wire_len = pkt_len;
	if (apple_z2_is_d11(z2))
		wire_len = max_t(size_t, wire_len,
				 APPLE_Z2_GEN2_MIN_RESULT_SIZE);

	if (apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) {
		if (apple_z2_is_j71b(z2)) {
			/* Sandcastle generation1 data phase: EB, tag, phase1. This
			 * acknowledges the queued report instead of generic A5 filler.
			 */
			memset(z2->rx_buf,0,wire_len);
			z2->rx_buf[0]=APPLE_Z2_CMD_READ_INTERRUPT_DATA;
			z2->rx_buf[1]=counter;z2->rx_buf[2]=1;
			/* J71b RX FIFO callback supplies16 command bytes to the common
			 * checksum/tag/phase validator even while TX streams a report.
			 */
			apple_z2_z2_checksum(z2->rx_buf,APPLE_Z2_CMD_SIZE);
		} else memset(z2->rx_buf, 0xa5, wire_len);
		xfer.tx_buf = z2->rx_buf;
		xfer.rx_buf = z2->rx_buf;
		xfer.len = wire_len;
		if (apple_z2_is_j71b(z2))
			error=j71b_exchange_separate(z2,z2->rx_buf,wire_len);
		else error = spi_sync_transfer(z2->spidev, &xfer, 1);
	} else {
		error = spi_read(z2->spidev, z2->rx_buf, pkt_len);
	}
	if (error)
		return error;
	if (apple_z2_is_iphone7_plus(z2))
		apple_z2_post_z2_xfer_delay(z2);

	/* Preserve the reference read/drain exchange, but do not manufacture a
	 * frame from an explicitly zero-length queue response. No event emitted.
	 */
	if (header_empty) {
		runtime_empty++;
		z2->runtime_queue_empty=true;
		return 0;
	}

	/* Sandcastle generation1 also returns zero/no-data while service is
	 * pending. Do not emit an event or advance the tag; bounded drain retries.
	 */
	if (apple_z2_is_j71b(z2) && z2->rx_buf[0]==0) {
		z2->runtime_control_reply=true;runtime_no_data++;
		return 0;
	}

	/* Native generation1 may return a control/not-ready reply during data
	 * service. It is not a coordinate frame. Validate its own16-byte CRC,
	 * keep the tag unchanged, and let the bounded drain continue.
	 */
	if (apple_z2_is_j71b(z2) && wire_len>=16 && z2->rx_buf[0]==APPLE_Z2_CMD_LAST &&
	    apple_z2_z2_checksum_valid(z2->rx_buf,16)) {
		z2->runtime_control_reply=true;runtime_controls++;
		return 0;
	}

	/* D11 touch reports do not follow the requested alternating tag. */
	strict_counter = !apple_z2_is_d11(z2) ||
			 (pkt_len > 5 && z2->rx_buf[5] ==
					 APPLE_Z2_D11_STATUS_REPORT);
	z2->runtime_frame_valid =
		apple_z2_gen2_packet_valid(z2->rx_buf, pkt_len, counter,
					   strict_counter);
	if (!z2->runtime_frame_valid) {
		if (apple_z2_is_j71b(z2)) {
			runtime_bad++;
			dev_warn_ratelimited(&z2->spidev->dev,"J71B_RUNTIME_INVALID_PACKET bytes=%zu header=%*ph\n",pkt_len,(int)min_t(size_t,pkt_len,64),z2->rx_buf);
		}
		if (apple_z2_is_j71b(z2) && z2->protocol_state < APPLE_Z2_STATE_RUNTIME)
			dev_info(&z2->spidev->dev,"J71B_RUNTIME_INITIAL_INVALID bytes=%zu header=%*ph\n",pkt_len,(int)min_t(size_t,pkt_len,32),z2->rx_buf);
		return 0;
	}

	if (apple_z2_is_j71b(z2)) runtime_packets++;
	payload_len = get_unaligned_le16(z2->rx_buf + 2);
	if (apple_z2_is_j71b(z2) && z2->protocol_state < APPLE_Z2_STATE_RUNTIME)
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_INITIAL_FRAME bytes=%zu header=%*ph\n",pkt_len,(int)min_t(size_t,pkt_len,32),z2->rx_buf);
	apple_z2_dispatch_frame(z2, z2->rx_buf + 5, payload_len - 2);
	z2->index_parity = !z2->index_parity;

	return 0;
}

static void apple_z2_reset_protocol(struct apple_z2 *z2)
{
	z2->protocol_state = APPLE_Z2_STATE_OFF;
	z2->index_parity = 0;
	z2->runtime_frame_valid = false;
	z2->surface_descriptor_valid = false;
}

static int apple_z2_advance_protocol(struct apple_z2 *z2,
				     enum apple_z2_protocol_state expected,
				     enum apple_z2_protocol_state next)
{
	if (z2->protocol_state != expected)
		return -EPROTO;

	z2->protocol_state = next;
	return 0;
}

static void apple_z2_z2_checksum(u8 *cmd, unsigned int len)
{
	u16 checksum = 0;
	int i;

	for (i = 0; i < len - 2; i++)
		checksum += cmd[i];
	put_unaligned_le16(checksum, cmd + len - 2);
}

static bool apple_z2_z2_checksum_valid(const u8 *cmd, unsigned int len)
{
	u16 checksum = 0;
	int i;

	if (len < 2)
		return false;

	for (i = 0; i < len - 2; i++)
		checksum += cmd[i];

	return get_unaligned_le16(cmd + len - 2) == checksum;
}

static void apple_z2_set_spi_delay(struct spi_delay *delay,
				   unsigned int usecs)
{
	if (usecs > U16_MAX)
		usecs = U16_MAX;

	delay->value = usecs;
	delay->unit = SPI_DELAY_UNIT_USECS;
}

static unsigned int apple_z2_effective_cs_delay_us(struct apple_z2 *z2)
{
	/* J172 firmware expresses this field in ns; Linux SPI uses us here. */
	if (apple_z2_is_j71b(z2)) return 1000;
	return apple_z2_uses_ipad_protocol(z2) ? 10 : z2->z2_cs_delay_us;
}

static void apple_z2_apply_z2_delays(struct apple_z2 *z2)
{
	unsigned int cs_delay_us = apple_z2_effective_cs_delay_us(z2);
	unsigned int inactive_us = apple_z2_is_j71b(z2) ? 1000 :
				  apple_z2_uses_ipad_protocol(z2) ? 10 :
				  z2->z2_inter_packet_delay_us;

	if (cs_delay_us) {
		apple_z2_set_spi_delay(&z2->spidev->cs_setup, cs_delay_us);
		apple_z2_set_spi_delay(&z2->spidev->cs_hold, apple_z2_is_j71b(z2) ? 0 : cs_delay_us);
	}
	if (inactive_us)
		apple_z2_set_spi_delay(&z2->spidev->cs_inactive, inactive_us);
}

static void apple_z2_post_z2_xfer_delay(struct apple_z2 *z2)
{
	unsigned int delay_us = z2->z2_inter_packet_delay_us;

	if (delay_us)
		usleep_range(delay_us, delay_us + 100);
}

static void apple_z2_post_ack_delay(struct apple_z2 *z2)
{
	unsigned int delay_us = apple_z2_effective_cs_delay_us(z2);

	if (delay_us)
		usleep_range(delay_us, delay_us + 100);
}

static void apple_z2_avoid_short_dma(struct apple_z2 *z2,
				     struct spi_transfer *xfer)
{
	/*
	 * iPhone 7 Plus firmware describes a target-specific minimum
	 * DMA transfer length, not a property of the SPI controller.  Supplying
	 * an RX buffer makes shorter writes full-duplex, which the T8010
	 * controller handles through PIO without changing the wire transfer.
	 */
	if (!apple_z2_is_iphone7_plus(z2) || !z2->dma_min_len ||
	    !xfer->tx_buf || xfer->rx_buf ||
	    xfer->len >= z2->dma_min_len)
		return;

	memset(z2->rx_buf, 0, xfer->len);
	xfer->rx_buf = z2->rx_buf;
}

static int apple_z2_z2_xfer_locked_delay(struct apple_z2 *z2,
					 const char *tag, bool post_delay)
{
	struct spi_transfer xfer = {
		.tx_buf = z2->tx_buf,
		.rx_buf = z2->rx_buf,
		.len = APPLE_Z2_CMD_SIZE,
	};
	int error;

	apple_z2_z2_checksum(z2->tx_buf, APPLE_Z2_CMD_SIZE);
	apple_z2_apply_z2_delays(z2);
	error = spi_sync_transfer(z2->spidev, &xfer, 1);
	if (error) {
		dev_err(&z2->spidev->dev, "%s transfer failed: %d\n",
			tag, error);
		return error;
	}
	if (post_delay)
		apple_z2_post_z2_xfer_delay(z2);

	dev_dbg(&z2->spidev->dev, "%s tx=%*ph rx=%*ph\n", tag,
		APPLE_Z2_CMD_SIZE, z2->tx_buf, APPLE_Z2_CMD_SIZE,
		z2->rx_buf);
	return 0;
}

static int apple_z2_z2_xfer_locked(struct apple_z2 *z2, const char *tag)
{
	return apple_z2_z2_xfer_locked_delay(z2, tag, true);
}

static int apple_z2_z2_cmd_locked_delay(struct apple_z2 *z2, u8 cmd,
					u8 report, const char *tag,
					bool post_delay)
{
	memset(z2->tx_buf, 0, APPLE_Z2_CMD_SIZE);
	memset(z2->rx_buf, 0, APPLE_Z2_CMD_SIZE);
	z2->tx_buf[0] = cmd;
	z2->tx_buf[1] = report;

	return apple_z2_z2_xfer_locked_delay(z2, tag, post_delay);
}

static int apple_z2_z2_cmd_locked(struct apple_z2 *z2, u8 cmd, u8 report,
				  const char *tag)
{
	return apple_z2_z2_cmd_locked_delay(z2, cmd, report, tag, true);
}

static int apple_z2_read_device_info_locked(struct apple_z2 *z2)
{
	u16 max_packet_size;
	u8 interface_version;
	u8 platform_id;
	int error;

	error = apple_z2_z2_cmd_locked_delay(z2, APPLE_Z2_CMD_WAKE, 0,
					     "device-info-wake", false);
	if (error)
		return error;
	usleep_range(2000, 2100);

	error = apple_z2_z2_cmd_locked_delay(z2, APPLE_Z2_CMD_DEVICE_INFO, 0,
					     "device-info", false);
	if (error)
		return error;
	apple_z2_post_z2_xfer_delay(z2);

	error = apple_z2_z2_cmd_locked_delay(z2, APPLE_Z2_CMD_LAST, 0,
					     "device-info-last", false);
	if (error)
		return error;

	if (z2->rx_buf[0] != APPLE_Z2_CMD_DEVICE_INFO ||
	    !apple_z2_z2_checksum_valid(z2->rx_buf, APPLE_Z2_CMD_SIZE)) {
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_DEVICE_INFO_INVALID %*ph\n",16,z2->rx_buf);
		return -EPROTO;
	}

	platform_id = z2->rx_buf[1];
	interface_version = z2->rx_buf[2];
	max_packet_size = get_unaligned_le16(z2->rx_buf + 3);

	dev_info(&z2->spidev->dev,
		"device info: platform=%u interface=%u max-packet=%u\n",
		 platform_id, interface_version, max_packet_size);
	return apple_z2_advance_protocol(z2, APPLE_Z2_STATE_FIRMWARE_READY,
					 APPLE_Z2_STATE_DEVICE_INFO);
}

static int apple_z2_write_report_locked(struct apple_z2 *z2, u8 report,
					const u8 *data, u8 len,
					const char *tag)
{
	char last_tag[32];
	int error;

	if (len > 11)
		return -EINVAL;

	memset(z2->tx_buf, 0, APPLE_Z2_CMD_SIZE);
	memset(z2->rx_buf, 0, APPLE_Z2_CMD_SIZE);
	z2->tx_buf[0] = APPLE_Z2_CMD_CTRL_WRITE_SHORT;
	z2->tx_buf[1] = report;
	z2->tx_buf[2] = len;
	memcpy(z2->tx_buf + 3, data, len);
	error = apple_z2_z2_xfer_locked(z2, tag);
	if (error)
		return error;

	memset(z2->tx_buf, 0, APPLE_Z2_CMD_SIZE);
	memset(z2->rx_buf, 0, APPLE_Z2_CMD_SIZE);
	z2->tx_buf[0] = APPLE_Z2_CMD_LAST;
	snprintf(last_tag, sizeof(last_tag), "%s-last", tag);
	error = apple_z2_z2_xfer_locked(z2, last_tag);
	if (error)
		return error;

	if (apple_z2_is_d11(z2) &&
	    (z2->rx_buf[0] != APPLE_Z2_CMD_LAST ||
	     !apple_z2_z2_checksum_valid(z2->rx_buf, APPLE_Z2_CMD_SIZE)))
		return -EPROTO;

	return 0;
}

static int apple_z2_enable_d11_reports_locked(struct apple_z2 *z2)
{
	static const u8 enable = 1;

	return apple_z2_write_report_locked(z2, APPLE_Z2_D11_REPORT_ENABLE,
					    &enable, sizeof(enable), "d11-e2");
}

static int apple_z2_read_report_info_locked(struct apple_z2 *z2, u8 report,
					    u8 *status, u16 *len,
					    const char *tag)
{
	char xfer_tag[40];
	unsigned int i;
	int error;

	for (i = 0; i < 2; i++) {
		snprintf(xfer_tag, sizeof(xfer_tag), "%s-info-%u", tag, i + 1);
		error = apple_z2_z2_cmd_locked(z2, APPLE_Z2_CMD_REPORT_INFO,
					       report, xfer_tag);
		if (error)
			return error;
	}

	if (z2->rx_buf[0] != APPLE_Z2_CMD_REPORT_INFO ||
	    z2->rx_buf[1] != report ||
	    !apple_z2_z2_checksum_valid(z2->rx_buf, APPLE_Z2_CMD_SIZE))
		return -EPROTO;

	*status = z2->rx_buf[2];
	*len = get_unaligned_le16(z2->rx_buf + 3);
	dev_info(&z2->spidev->dev,
		"%s report=%02x status=%u len=%u\n", tag, report,
		*status, *len);

	return 0;
}

/* Framed second phase used by Sandcastle hx-touchd's type1 read_report.
 * Exact J71b firmware E7 handler reads length at3/4, copies phase byte2 and
 * checks the whole request's trailing LE16 byte sum. HBPP boot format alone
 * does not determine whether A5 filler is valid for this runtime operation.
 */
static int j71b_prepare_feature_read(u8 *buffer,size_t capacity,u8 report,u16 len)
{
 size_t bytes=(size_t)len+5;
 if(!len || bytes>capacity || bytes>=0x282)return -EMSGSIZE;
 memset(buffer,0,bytes);
 buffer[0]=APPLE_Z2_CMD_CTRL_READ_LONG;buffer[1]=report;buffer[2]=1;
 put_unaligned_le16(len,buffer+3);
 apple_z2_z2_checksum(buffer,bytes);
 return 0;
}

static int apple_z2_read_report_locked(struct apple_z2 *z2, u8 report,
				       u16 len, const char *tag)
{
	struct spi_transfer xfer = { };
	char last_tag[32];
	u8 command_reply[16]={0};
	unsigned int data_len;
	int error;

	if (!len || len > APPLE_Z2_RX_BUF_SIZE - 5)
		return -EMSGSIZE;

	memset(z2->tx_buf, 0, APPLE_Z2_CMD_SIZE);
	memset(z2->rx_buf, 0, APPLE_Z2_CMD_SIZE);
	z2->tx_buf[0] = len <= 11 ? APPLE_Z2_CMD_CTRL_READ_SHORT :
					 APPLE_Z2_CMD_CTRL_READ_LONG;
	z2->tx_buf[1] = report;
	put_unaligned_le16(len, z2->tx_buf + 3);
	error = apple_z2_z2_xfer_locked(z2, tag);
	if (error)
		return error;
	if (apple_z2_is_j71b(z2) && len>11) memcpy(command_reply,z2->rx_buf,16);

	if (len <= 11) {
		snprintf(last_tag, sizeof(last_tag), "%s-last", tag);
		error = apple_z2_z2_cmd_locked(z2, APPLE_Z2_CMD_LAST, 0,
					       last_tag);
		if (!error)
			dev_dbg(&z2->spidev->dev, "%s short read done\n",
				tag);
		return error;
	}

	data_len = len + 5;
	if (apple_z2_is_j71b(z2)) {
		error=j71b_prepare_feature_read(z2->rx_buf,APPLE_Z2_RX_BUF_SIZE,report,len);
		if(error)return error;
	} else memset(z2->rx_buf, 0xa5, data_len);
	xfer.tx_buf = z2->rx_buf;
	xfer.rx_buf = z2->rx_buf;
	xfer.len = data_len;
	apple_z2_apply_z2_delays(z2);
	if (apple_z2_is_j71b(z2))
		error=j71b_exchange_separate(z2,z2->rx_buf,data_len);
	else error = spi_sync_transfer(z2->spidev, &xfer, 1);
	if (error)
		return error;
	apple_z2_post_z2_xfer_delay(z2);
	if (apple_z2_is_j71b(z2))
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_FEATURE_COMMAND tag=%s tx=%*ph immediate-rx=%*ph\n",tag,16,z2->tx_buf,16,command_reply);
	dev_dbg(&z2->spidev->dev, "%s long read len=%u\n", tag, data_len);

	return 0;
}

static int apple_z2_store_surface_descriptor(struct apple_z2 *z2, u16 len)
{
	const u8 *descriptor = z2->rx_buf + 3;
	unsigned int x_pixels;
	unsigned int y_pixels;
	unsigned int x_res;
	unsigned int y_res;
	s16 max_x;
	s16 min_x;
	s16 max_y;
	s16 min_y;
	u32 width;
	u32 height;

	if (apple_z2_is_j71b(z2))
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_SURFACE_RAW len=%u bytes=%*ph\n",len,(int)min_t(unsigned int,len+5,32),z2->rx_buf);
	if (len != APPLE_Z2_SURFACE_DESCRIPTOR_SIZE ||
	    z2->rx_buf[0] != APPLE_Z2_CMD_CTRL_READ_LONG ||
	    z2->rx_buf[1] != APPLE_Z2_REPORT_SURFACE ||
	    (apple_z2_is_j71b(z2) ? z2->rx_buf[2]>1 : z2->rx_buf[2]!=0) ||
	    !apple_z2_z2_checksum_valid(z2->rx_buf, len + 5))
		return -EPROTO;

	width = get_unaligned_le32(descriptor);
	height = get_unaligned_le32(descriptor + 4);
	min_x = (s16)get_unaligned_le16(descriptor + 8);
	max_x = (s16)get_unaligned_le16(descriptor + 12);
	min_y = (s16)get_unaligned_le16(descriptor + 10);
	max_y = (s16)get_unaligned_le16(descriptor + 14);
	if (!width || !height || max_x <= min_x || max_y <= min_y)
		return -EPROTO;

	z2->sensor_max_x = max_x;
	z2->sensor_min_x = min_x;
	z2->sensor_max_y = max_y;
	z2->sensor_min_y = min_y;
	z2->surface_descriptor_valid = true;

	x_pixels = input_abs_get_max(z2->input_dev, ABS_MT_POSITION_X) -
		   input_abs_get_min(z2->input_dev, ABS_MT_POSITION_X) + 1;
	y_pixels = input_abs_get_max(z2->input_dev, ABS_MT_POSITION_Y) -
		   input_abs_get_min(z2->input_dev, ABS_MT_POSITION_Y) + 1;
	x_res = max(DIV_ROUND_CLOSEST(x_pixels * 100, width), 1U);
	y_res = max(DIV_ROUND_CLOSEST(y_pixels * 100, height), 1U);
	input_abs_set_res(z2->input_dev, ABS_MT_POSITION_X, x_res);
	input_abs_set_res(z2->input_dev, ABS_MT_POSITION_Y, y_res);
	if (test_bit(ABS_X, z2->input_dev->absbit))
		input_abs_set_res(z2->input_dev, ABS_X, x_res);
	if (test_bit(ABS_Y, z2->input_dev->absbit))
		input_abs_set_res(z2->input_dev, ABS_Y, y_res);

	dev_info(&z2->spidev->dev,
		"surface %ux%u, bounds x=%d..%d y=%d..%d, resolution %ux%u units/mm\n",
		 width, height, min_x, max_x, min_y, max_y, x_res, y_res);

	return 0;
}

static int apple_z2_iphone7_plus_init_locked(struct apple_z2 *z2)
{
	static const u8 disable_legacy_reports[] = { 0 };
	u16 surface_len;
	u8 status;
	int error;

	error = apple_z2_z2_cmd_locked(z2, APPLE_Z2_CMD_WAKE, 0,
				       "iphone7-plus-wake");
	if (error)
		return error;

	error = apple_z2_read_report_info_locked(z2, APPLE_Z2_REPORT_SURFACE,
						 &status, &surface_len,
						 "iphone7-plus-d9");
	if (error)
		return error;
	if (status || surface_len != APPLE_Z2_SURFACE_DESCRIPTOR_SIZE)
		return -EPROTO;

	error = apple_z2_read_report_locked(z2, APPLE_Z2_REPORT_SURFACE,
					    surface_len,
					    "iphone7-plus-d9-read");
	if (error)
		return error;
	error = apple_z2_store_surface_descriptor(z2, surface_len);
	if (error)
		return error;

	error = apple_z2_write_report_locked(z2, APPLE_Z2_REPORT_ENABLE,
					     disable_legacy_reports,
					     sizeof(disable_legacy_reports),
					     "iphone7-plus-af");
	if (error)
		return error;

	dev_dbg(&z2->spidev->dev,
		"iPhone 7 Plus report initialization complete\n");

	return 0;
}

static int apple_z2_j172_init_reads_locked(struct apple_z2 *z2)
{
	u8 status;
	u16 db_len;
	u16 prop_len;
	u16 surface_len;
	bool has_prop73=true;
	unsigned int i;
	int error;

	error = apple_z2_read_report_info_locked(z2, APPLE_Z2_REPORT_SURFACE,
						 &status, &surface_len, "d9");
	if (error)
		return error;
	if (status || surface_len != APPLE_Z2_SURFACE_DESCRIPTOR_SIZE)
		return -EPROTO;

	error = apple_z2_read_report_locked(z2, APPLE_Z2_REPORT_SURFACE,
					    surface_len, "d9-read");
	if (error)
		return error;
	error = apple_z2_store_surface_descriptor(z2, surface_len);
	if (error)
		return error;

	error = apple_z2_read_report_info_locked(z2, 0xdb, &status, &db_len,
						 "db");
	if (error)
		return error;
	if (status || db_len != 0x7d)
		return -EPROTO;

	error = apple_z2_read_report_locked(z2, 0xdb, db_len, "db-read-1");
	if (error)
		return error;

	error = apple_z2_read_report_info_locked(z2, 0x73, &status, &prop_len,
						 "73");
	if (error)
		return error;
	if (apple_z2_is_j71b(z2) && status==2 && !prop_len) {
		has_prop73=false;
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_OPTIONAL_REPORT_73_UNSUPPORTED\n");
	} else if (status || prop_len != 8) return -EPROTO;

	if (has_prop73) {
		error = apple_z2_read_report_locked(z2, 0x73, prop_len, "73-read-1");
		if (error) return error;
	}

	for (i = 2; i <= 3; i++) {
		char db_tag[16];
		char prop_tag[16];

		snprintf(db_tag, sizeof(db_tag), "db-read-%u", i);
		error = apple_z2_read_report_locked(z2, 0xdb, db_len, db_tag);
		if (error)
			return error;

		snprintf(prop_tag, sizeof(prop_tag), "73-read-%u", i);
		if (has_prop73) {
			error = apple_z2_read_report_locked(z2, 0x73, prop_len, prop_tag);
			if (error) return error;
		}
	}

	return 0;
}

static int apple_z2_j172_init_writes_locked(struct apple_z2 *z2)
{
	static const u8 rep9d[] = { 0x01, 0x00, 0x00, 0x00,
				    0x00, 0x00, 0x00, 0x00 };
	static const u8 repbf[] = { 0x9d, 0x81, 0x09, 0x00 };
	u8 status;
	u16 len;
	int error;

	error = apple_z2_z2_cmd_locked(z2, APPLE_Z2_CMD_WAKE, 0, "j172-wake");
	if (error)
		return error;
	usleep_range(2200, 2300);

	error = apple_z2_read_report_info_locked(z2, 0x9d, &status, &len,
						 "9d");
	if (error)
		return error;
	if (apple_z2_is_j71b(z2) && status==2 && !len) {
		/* Exact J71b image GET/SET dispatches do not implement9d. BF is
		 * paired with that J172-only mode setup; do not write it blindly.
		 */
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_DEFAULT_REPORT_CONFIGURATION no-J172-9d-bf-writes\n");
		return apple_z2_advance_protocol(z2,APPLE_Z2_STATE_RUNTIME,APPLE_Z2_STATE_REPORTS_READY);
	}
	if (status || len != sizeof(rep9d))
		return -EPROTO;

	error = apple_z2_write_report_locked(z2, 0x9d, rep9d,
					     sizeof(rep9d), "j172-9d-1");
	if (error)
		return error;
	usleep_range(50500, 50700);
	error = apple_z2_write_report_locked(z2, 0x9d, rep9d,
					     sizeof(rep9d), "j172-9d-2");
	if (error)
		return error;

	error = apple_z2_read_report_info_locked(z2, 0xbf, &status, &len,
						 "bf");
	if (error)
		return error;
	if (status || len != sizeof(repbf))
		return -EPROTO;
	error = apple_z2_write_report_locked(z2, 0xbf, repbf,
					     sizeof(repbf), "j172-bf");
	if (error)
		return error;

	error = apple_z2_read_report_info_locked(z2, 0xaf, &status, &len,
						 "af");
	if (error)
		return error;
	if (status != 2 || len)
		return -EPROTO;

	dev_dbg(&z2->spidev->dev, "J172 report initialization complete\n");
	return apple_z2_advance_protocol(z2, APPLE_Z2_STATE_RUNTIME,
					 APPLE_Z2_STATE_REPORTS_READY);
}

/* Same bounded generation1 drain used by Sandcastle. An asserted IRQ must
 * be serviced through report acknowledgement, not by manufacturing an edge.
 */
static int j71b_drain_reports(struct apple_z2 *z2)
{
 unsigned int i;
 int error;
 for(i=0;i<4;i++){
  error=apple_z2_read_packet(z2);
  if(error)return error;
  if(z2->runtime_queue_empty || (!z2->runtime_frame_valid && !z2->runtime_control_reply))break;
 }
 return 0;
}

static irqreturn_t apple_z2_irq(int irq, void *data)
{
	struct apple_z2 *z2 = data;
	int error;

	if (unlikely(!z2->booted)) {
		if (apple_z2_is_j71b(z2)) return j71b_readiness_irq(irq,&z2->verified_boot);
		complete(&z2->boot_irq);
	} else {
		if (apple_z2_is_j71b(z2)) runtime_irqs++;
		mutex_lock(&z2->io_lock);
		error = apple_z2_is_j71b(z2) ? j71b_drain_reports(z2) : apple_z2_read_packet(z2);
		mutex_unlock(&z2->io_lock);
		if (error)
			dev_warn_ratelimited(&z2->spidev->dev,
					     "runtime packet read failed: %d\n",
					     error);
	}

	return IRQ_HANDLED;
}

static void apple_z2_set_gpio(struct gpio_desc *gpio, int value)
{
	if (gpio)
		gpiod_set_value_cansleep(gpio, value);
}

static void apple_z2_pulse_gpio_pair(struct gpio_desc *gpio0,
				     struct gpio_desc *gpio1)
{
	apple_z2_set_gpio(gpio0, 1);
	apple_z2_set_gpio(gpio1, 1);
	usleep_range(1000, 2000);
	apple_z2_set_gpio(gpio0, 0);
	apple_z2_set_gpio(gpio1, 0);
	usleep_range(1000, 2000);
}

static int apple_z2_j172_sync_claim_gpios(struct apple_z2 *z2)
{
	struct device *dev = &z2->spidev->dev;
	int error;

	if (!z2->display_sync1_gpio) {
		z2->display_sync1_gpio =
			devm_gpiod_get_optional(dev, "display-sync1",
						GPIOD_OUT_LOW);
		if (IS_ERR(z2->display_sync1_gpio)) {
			error = PTR_ERR(z2->display_sync1_gpio);
			z2->display_sync1_gpio = NULL;
			return error;
		}
	}

	if (!z2->display_sync_gpio) {
		z2->display_sync_gpio =
			devm_gpiod_get_optional(dev, "display-sync",
						GPIOD_OUT_LOW);
		if (IS_ERR(z2->display_sync_gpio)) {
			error = PTR_ERR(z2->display_sync_gpio);
			z2->display_sync_gpio = NULL;
			return error;
		}
	}

	if (!z2->display_sync_gpio || !z2->display_sync1_gpio)
		return -ENODEV;

	return 0;
}

static int apple_z2_j172_sync_set_inactive(struct apple_z2 *z2)
{
	int error;

	if (z2->j172_sync_pinctrl_active) {
		devm_pinctrl_put(z2->j172_sync_pinctrl);
		z2->j172_sync_pinctrl = NULL;
		z2->j172_sync_active_state = NULL;
		z2->j172_sync_pinctrl_active = false;
	}

	error = apple_z2_j172_sync_claim_gpios(z2);
	if (error)
		return error;

	error = gpiod_direction_input(z2->display_sync1_gpio);
	if (error)
		return error;

	error = gpiod_direction_input(z2->display_sync_gpio);
	if (error)
		return error;

	dev_dbg(&z2->spidev->dev, "display-sync pins inactive\n");
	return 0;
}

static int apple_z2_j172_sync_set_active(struct apple_z2 *z2)
{
	struct device *dev = &z2->spidev->dev;
	int error;

	error = apple_z2_j172_sync_set_inactive(z2);
	if (error)
		return error;

	devm_gpiod_put(dev, z2->display_sync1_gpio);
	z2->display_sync1_gpio = NULL;
	devm_gpiod_put(dev, z2->display_sync_gpio);
	z2->display_sync_gpio = NULL;

	z2->j172_sync_pinctrl = devm_pinctrl_get(dev);
	if (IS_ERR(z2->j172_sync_pinctrl)) {
		error = PTR_ERR(z2->j172_sync_pinctrl);
		z2->j172_sync_pinctrl = NULL;
		goto reclaim_gpios;
	}

	z2->j172_sync_active_state =
		pinctrl_lookup_state(z2->j172_sync_pinctrl,
				     "display-sync-active");
	if (IS_ERR(z2->j172_sync_active_state)) {
		error = PTR_ERR(z2->j172_sync_active_state);
		z2->j172_sync_active_state = NULL;
		goto put_pinctrl;
	}

	error = pinctrl_select_state(z2->j172_sync_pinctrl,
				     z2->j172_sync_active_state);
	if (error)
		goto put_pinctrl;

	z2->j172_sync_pinctrl_active = true;
	dev_dbg(dev, "display-sync pins active\n");
	return 0;

put_pinctrl:
	devm_pinctrl_put(z2->j172_sync_pinctrl);
	z2->j172_sync_pinctrl = NULL;
	z2->j172_sync_active_state = NULL;
reclaim_gpios:
	if (apple_z2_j172_sync_claim_gpios(z2))
		dev_warn(dev, "failed to reclaim display-sync GPIOs\n");
	return error;
}

static int j71b_level_service(struct apple_z2 *z2)
{
 int ret;
 z2->saved_irq_type=irq_get_trigger_type(z2->spidev->irq);
 if(!z2->saved_irq_type)return -EINVAL;
 ret=irq_set_irq_type(z2->spidev->irq,IRQ_TYPE_LEVEL_LOW);
 if(ret)return ret;
 z2->irq_type_changed=true;
 dev_info(&z2->spidev->dev,"J71B_RUNTIME_IRQ_LEVEL_LOW saved-type=%u\n",z2->saved_irq_type);
 return 0;
}
static void j71b_restore_irq_type(struct apple_z2 *z2)
{
 int ret;
 if(!z2->irq_type_changed)return;
 ret=irq_set_irq_type(z2->spidev->irq,z2->saved_irq_type);
 if(ret)dev_warn(&z2->spidev->dev,"J71B_RUNTIME_IRQ_RESTORE_ERROR %d\n",ret);
 else z2->irq_type_changed=false;
}

static void apple_z2_platform_power_off(struct apple_z2 *z2)
{
	int error;

	if (apple_z2_is_j71b(z2)) j71b_restore_irq_type(z2);
	if (apple_z2_is_iphone7_plus(z2))
		gpiod_set_value_cansleep(z2->reset_gpio, 1);

	if (z2->clk_enabled) {
		clk_disable_unprepare(z2->clk);
		z2->clk_enabled = false;
	}
	if (apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2))
		usleep_range(1000, 2000);

	if (apple_z2_is_j71b(z2)) {
		apple_z2_set_gpio(z2->power_ldo_gpio,0);
		apple_z2_set_gpio(z2->power_ana_gpio,0);
		z2->platform_powered=false;
		dev_info(&z2->spidev->dev,"J71B_RUNTIME_POWER_OFF reset-path rails=off clock=off\n");
		return;
	}
	if (apple_z2_is_iphone7_plus(z2)) {
		if (z2->core_enabled) {
			error = regulator_disable(z2->core_supply);
			if (error) {
				dev_warn(&z2->spidev->dev,
					 "failed to disable core supply: %d\n", error);
			} else {
				z2->core_enabled = false;
			}
			usleep_range(1000, 2000);
		}
		if (z2->hv_enabled) {
			error = regulator_disable(z2->hv_supply);
			if (error) {
				dev_warn(&z2->spidev->dev,
					 "failed to disable HV supply: %d\n", error);
			} else {
				z2->hv_enabled = false;
			}
		}
		z2->platform_powered = false;
		return;
	}

	if (apple_z2_uses_ipad_protocol(z2)) {
		error = apple_z2_j172_sync_set_inactive(z2);
		if (error)
			dev_warn(&z2->spidev->dev,
				 "failed to set display-sync inactive: %d\n",
				 error);
	} else {
		apple_z2_set_gpio(z2->display_sync1_gpio, 0);
		apple_z2_set_gpio(z2->display_sync_gpio, 0);
	}
	apple_z2_set_gpio(z2->power_ldo_gpio, 0);
	apple_z2_set_gpio(z2->power_ana_gpio, 0);
	z2->platform_powered = false;
}

static int apple_z2_enable_clock(struct apple_z2 *z2)
{
	int error;

	if (!z2->clk || z2->clk_enabled)
		return 0;

	error = clk_prepare_enable(z2->clk);
	if (error)
		return error;

	z2->clk_enabled = true;
	return 0;
}

static int apple_z2_j172_power_on(struct apple_z2 *z2)
{
	int error;

	apple_z2_platform_power_off(z2);
	gpiod_set_value_cansleep(z2->reset_gpio, 1);
	msleep(50);

	apple_z2_set_gpio(z2->power_ana_gpio, 1);
	apple_z2_set_gpio(z2->power_ldo_gpio, 1);
	error = apple_z2_j172_sync_set_active(z2);
	if (error) {
		apple_z2_platform_power_off(z2);
		return error;
	}

	error = apple_z2_enable_clock(z2);
	if (error) {
		apple_z2_platform_power_off(z2);
		return error;
	}

	usleep_range(15000, 16000);
	z2->platform_powered = true;
	return apple_z2_advance_protocol(z2, APPLE_Z2_STATE_OFF,
					 APPLE_Z2_STATE_POWERED);
}

static int apple_z2_iphone7_plus_power_on(struct apple_z2 *z2)
{
	int error;

	gpiod_set_value_cansleep(z2->reset_gpio, 1);

	error = regulator_enable(z2->hv_supply);
	if (error)
		return error;
	z2->hv_enabled = true;
	usleep_range(2000, 5000);

	error = regulator_enable(z2->core_supply);
	if (error)
		goto err_power_off;
	z2->core_enabled = true;
	usleep_range(2000, 5000);

	error = apple_z2_enable_clock(z2);
	if (error)
		goto err_power_off;
	usleep_range(1000, 2000);

	z2->platform_powered = true;
	z2->protocol_state = APPLE_Z2_STATE_POWERED;
	return 0;

err_power_off:
	apple_z2_platform_power_off(z2);
	return error;
}

static int apple_z2_j71b_power_on(struct apple_z2 *z2)
{
 int error;
 gpiod_set_value_cansleep(z2->reset_gpio,1);
 apple_z2_platform_power_off(z2);
 msleep(50);
 apple_z2_set_gpio(z2->power_ana_gpio,1);usleep_range(10000,11000);
 apple_z2_set_gpio(z2->power_ldo_gpio,1);usleep_range(1000,2000);
 error=apple_z2_enable_clock(z2);
 if(error){apple_z2_platform_power_off(z2);return error;}
 usleep_range(15000,16000);
 z2->platform_powered=true;z2->protocol_state=APPLE_Z2_STATE_POWERED;
 return 0;
}

static int apple_z2_platform_power_on(struct apple_z2 *z2)
{
	int error;

	if (z2->platform_powered)
		return 0;

	if (apple_z2_is_j71b(z2))
		return apple_z2_j71b_power_on(z2);
	if (apple_z2_uses_ipad_protocol(z2))
		return apple_z2_j172_power_on(z2);
	if (apple_z2_is_iphone7_plus(z2))
		return apple_z2_iphone7_plus_power_on(z2);

	apple_z2_set_gpio(z2->power_ana_gpio, 1);
	usleep_range(1000, 2000);
	apple_z2_set_gpio(z2->power_ldo_gpio, 1);
	usleep_range(1000, 2000);
	apple_z2_pulse_gpio_pair(z2->display_sync_gpio,
				 z2->display_sync1_gpio);

	error = apple_z2_enable_clock(z2);
	if (error) {
		apple_z2_platform_power_off(z2);
		return error;
	}

	usleep_range(1000, 2000);
	z2->platform_powered = true;
	z2->protocol_state = APPLE_Z2_STATE_POWERED;
	return 0;
}

static int apple_z2_pulse_reset(struct apple_z2 *z2)
{
	gpiod_set_value_cansleep(z2->reset_gpio, 1);
	usleep_range(1000, 2000);
	gpiod_set_value_cansleep(z2->reset_gpio, 0);
	usleep_range(1000, 2000);
	return 0;
}

/* Build calibration blob, caller is responsible for freeing the blob data. */
static const char *apple_z2_calibration_property(u32 provider)
{
	switch (provider) {
	case APPLE_Z2_CAL_MULTI_TOUCH:
		return CAL_PROP_NAME;
	case APPLE_Z2_CAL_ORB_GAP:
		return ORB_GAP_CAL_PROP_NAME;
	case APPLE_Z2_CAL_ORB_FORCE:
		return ORB_FORCE_CAL_PROP_NAME;
	case APPLE_Z2_CAL_SHAPE_ACCEL:
		return SHAPE_ACCEL_CAL_PROP_NAME;
	default:
		return NULL;
	}
}

static const u8 *apple_z2_build_cal_blob(struct apple_z2 *z2,
					 const char *property, u32 address,
					 u32 max_size, size_t *size,
					 bool required)
{
	u8 *cal_data;
	int cal_size;
	size_t padded_size;
	size_t blob_size;
	u32 checksum;
	u16 checksum_hdr;
	int i;
	struct apple_z2_hbpp_blob_hdr *hdr;
	int error;

	if (!device_property_present(&z2->spidev->dev, property))
		return required ? ERR_PTR(-ENOENT) : NULL;

	cal_size = device_property_count_u8(&z2->spidev->dev, property);
	if (cal_size <= 0)
		return ERR_PTR(cal_size ?: -EINVAL);
	if (cal_size > max_size)
		return ERR_PTR(-E2BIG);

	padded_size = round_up((size_t)cal_size, sizeof(__le32));
	blob_size = sizeof(struct apple_z2_hbpp_blob_hdr) + padded_size +
		    sizeof(__le32);
	u8 *blob_data __free(kfree) = kzalloc(blob_size, GFP_KERNEL);
	if (!blob_data)
		return ERR_PTR(-ENOMEM);

	hdr = (struct apple_z2_hbpp_blob_hdr *)blob_data;
	hdr->cmd = cpu_to_le16(APPLE_Z2_HBPP_CMD_BLOB);
	hdr->len = cpu_to_le16(padded_size / sizeof(__le32));
	hdr->addr = cpu_to_le32(address);

	checksum_hdr = 0;
	for (i = 2; i < 8; i++)
		checksum_hdr += blob_data[i];
	hdr->checksum = cpu_to_le16(checksum_hdr);

	cal_data = blob_data + sizeof(struct apple_z2_hbpp_blob_hdr);
	error = device_property_read_u8_array(&z2->spidev->dev, property,
					      cal_data, cal_size);
	if (error)
		return ERR_PTR(error);

	checksum = 0;
	for (i = 0; i < cal_size; i++)
		checksum += cal_data[i];
	put_unaligned_le32(checksum, cal_data + padded_size);

	*size = blob_size;
	return no_free_ptr(blob_data);
}

/* Build the HBPP14 calibration packet used by iPhone 7 Plus Gen2 firmware. */
static const u8 *apple_z2_build_iphone7_cal(struct apple_z2 *z2,
					    const char *property, u32 address,
					    u32 max_size, size_t *size)
{
	u8 *cal_data;
	int cal_size;
	size_t padded_size;
	size_t words;
	size_t blob_size;
	u32 checksum;
	u16 header_checksum;
	unsigned int i;
	int error;

	if (!device_property_present(&z2->spidev->dev, property))
		return ERR_PTR(-ENOENT);

	cal_size = device_property_count_u8(&z2->spidev->dev, property);
	if (cal_size <= 0)
		return ERR_PTR(cal_size ?: -EINVAL);
	if (cal_size > max_size)
		return ERR_PTR(-E2BIG);

	padded_size = round_up((size_t)cal_size, sizeof(__le32));
	words = padded_size / sizeof(__le32);
	if (words > U16_MAX)
		return ERR_PTR(-E2BIG);
	blob_size = 12 + padded_size + sizeof(__le32);
	u8 *blob_data __free(kfree) = kzalloc(blob_size, GFP_KERNEL);
	if (!blob_data)
		return ERR_PTR(-ENOMEM);

	put_unaligned_be16(0x18e1, blob_data);
	put_unaligned_be16(APPLE_Z2_HBPP_CMD_BLOB, blob_data + 2);
	/* HBPP 0x103 encodes the full number of four-byte payload words. */
	put_unaligned_be16(words, blob_data + 4);
	put_unaligned_be16(address, blob_data + 6);
	put_unaligned_be16(address >> 16, blob_data + 8);
	header_checksum = 0;
	for (i = 4; i < 10; i++)
		header_checksum += blob_data[i];
	put_unaligned_be16(header_checksum, blob_data + 10);

	cal_data = blob_data + 12;
	error = device_property_read_u8_array(&z2->spidev->dev, property,
					      cal_data, cal_size);
	if (error)
		return ERR_PTR(error);

	checksum = 0;
	for (i = 0; i < cal_size; i++)
		checksum += cal_data[i];
	for (i = 0; i < padded_size; i += 2)
		swap(cal_data[i], cal_data[i + 1]);
	put_unaligned_be16(checksum, cal_data + padded_size);
	put_unaligned_be16(checksum >> 16, cal_data + padded_size + 2);

	*size = blob_size;
	return no_free_ptr(blob_data);
}

static int apple_z2_send_firmware_blob(struct apple_z2 *z2, const u8 *data,
				       u32 size, bool init, bool defer_irq_wait)
{
	struct spi_transfer blob_xfer = {
		.tx_buf = data,
		.len = size,
		.bits_per_word = 8,
	};
	struct spi_transfer ack_xfer = {
		.tx_buf = z2->tx_buf,
		.rx_buf = z2->rx_buf,
		.len = 2,
		.bits_per_word = 8,
	};
	bool ready;
	int error;

	/*
	 * iPhone 7 Plus firmware stores HBPP byte streams in 8-bit wire order.
	 * SmartIO may still DMA these transfers, but using 16-bit SPI words would
	 * swap every byte pair a second time.
	 */
	if (!init && !apple_z2_is_iphone7_plus(z2) &&
	    size >= z2->bpw16_min_len)
		blob_xfer.bits_per_word = 16;

	if (init && apple_z2_is_j71b(z2)) blob_xfer.speed_hz=500000;
	dev_info(&z2->spidev->dev, "firmware blob len=%u bpw=%u\n",
		size, blob_xfer.bits_per_word);

	if ((apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) && init) {
		if (size != 4 || data[0] != 0x1a || data[1] != 0xa1 ||
		    data[2] != 0x18 || data[3] != 0xe1)
			return dev_err_probe(&z2->spidev->dev, -EINVAL,
					     "invalid HBPP14 readiness command\n");
		memset(z2->rx_buf, 0, size);
		blob_xfer.rx_buf = z2->rx_buf;
	}
	apple_z2_avoid_short_dma(z2, &blob_xfer);

	z2->tx_buf[0] = 0x1a;
	z2->tx_buf[1] = 0xa1;
	apple_z2_avoid_short_dma(z2, &ack_xfer);
	reinit_completion(&z2->boot_irq);
	error = spi_sync_transfer(z2->spidev, &blob_xfer, 1);
	if (error)
		return error;

	if ((apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) && init) {
		ready = z2->rx_buf[0] == 0x1f && z2->rx_buf[1] == 0x01;
		if (!ready)
			return dev_err_probe(&z2->spidev->dev, -EPROTO,
					     "touch controller not ready\n");
		dev_dbg(&z2->spidev->dev, "HBPP14 ready\n");
	}

	if (!init || !z2->no_init_ack) {
		error = spi_sync_transfer(z2->spidev, &ack_xfer, 1);
		if (error)
			return error;
		apple_z2_post_ack_delay(z2);
	}
	if ((apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) && init) {
		error = apple_z2_advance_protocol(z2, APPLE_Z2_STATE_BOOT_IRQ,
						  APPLE_Z2_STATE_HBPP_READY);
		if (error)
			return error;
	}

	if (defer_irq_wait && z2->upload_irq_masked) {
		z2->upload_irq_masked = false;
		enable_irq(z2->spidev->irq);
	}

	if (!defer_irq_wait)
		wait_for_completion_timeout(&z2->boot_irq,
					    msecs_to_jiffies(20));

	return 0;
}

static int apple_z2_send_firmware_raw_xfer(struct apple_z2 *z2,
					   const u8 *data, u32 size)
{
	struct spi_transfer xfer = { };
	const u8 *logical_tx;
	u32 tx_len, rx_len, flags, xfer_len;
	unsigned int i;
	int error;

	if (size < 3 * sizeof(__le32))
		return -EINVAL;

	tx_len = get_unaligned_le32(data);
	rx_len = get_unaligned_le32(data + sizeof(__le32));
	flags = get_unaligned_le32(data + 2 * sizeof(__le32));
	if (tx_len > APPLE_Z2_RAW_XFER_MAX_SIZE ||
	    rx_len > APPLE_Z2_RAW_XFER_MAX_SIZE ||
	    flags & ~APPLE_Z2_RAW_XFER_RX_BIT_REVERSE ||
	    size != 3 * sizeof(__le32) + tx_len ||
	    (!tx_len && !rx_len))
		return -EINVAL;

	xfer_len = max(tx_len, rx_len);
	if ((tx_len && tx_len != xfer_len) ||
	    (rx_len && rx_len != xfer_len))
		return -EINVAL;

	logical_tx = data + 3 * sizeof(__le32);
	if (tx_len)
		xfer.tx_buf = logical_tx;
	if (rx_len) {
		memset(z2->rx_buf, 0, rx_len);
		xfer.rx_buf = z2->rx_buf;
	}
	xfer.len = xfer_len;
	xfer.bits_per_word = 8;
	apple_z2_avoid_short_dma(z2, &xfer);

	error = spi_sync_transfer(z2->spidev, &xfer, 1);
	if (error)
		return error;
	if (flags & APPLE_Z2_RAW_XFER_RX_BIT_REVERSE) {
		for (i = 0; i < rx_len; i++)
			z2->rx_buf[i] = bitrev8(z2->rx_buf[i]);
	}

	dev_dbg(&z2->spidev->dev,
		"firmware raw xfer tx_len=%u rx_len=%u flags=%#x tx=%*ph rx=%*ph\n",
		 tx_len, rx_len, flags, min(tx_len, 16U), logical_tx,
		 min(rx_len, 16U), z2->rx_buf);

	return 0;
}

static bool apple_z2_wait_firmware_irq(struct apple_z2 *z2, u32 timeout_ms)
{
	unsigned long timeout = msecs_to_jiffies(timeout_ms);

	if (!timeout)
		timeout = msecs_to_jiffies(1);

	if (!wait_for_completion_timeout(&z2->boot_irq, timeout)) {
		dev_warn(&z2->spidev->dev,
			 "firmware IRQ wait timed out after %u ms\n", timeout_ms);
		return false;
	}

	return true;
}

static int apple_z2_wait_ready_irq(struct apple_z2 *z2, u32 timeout_ms)
{
	if (!apple_z2_wait_firmware_irq(z2, timeout_ms))
		return -ETIMEDOUT;
	if (!apple_z2_uses_ipad_protocol(z2) && !apple_z2_is_iphone7_plus(z2))
		return 0;

	if (apple_z2_uses_ipad_protocol(z2) && !apple_z2_is_j71b(z2) &&
	    !apple_z2_wait_firmware_irq(z2, timeout_ms))
		return -ETIMEDOUT;

	dev_dbg(&z2->spidev->dev, "firmware ready IRQ%s received\n",
		apple_z2_uses_ipad_protocol(z2) ? "s" : "");
	return apple_z2_advance_protocol(z2, APPLE_Z2_STATE_HBPP_READY,
					 APPLE_Z2_STATE_FIRMWARE_READY);
}

static u32 apple_z2_fw_config_word(const u8 *data, unsigned int word)
{
	return get_unaligned_le32(data + word * sizeof(__le32));
}

static int apple_z2_apply_fw_config(struct apple_z2 *z2, const u8 *data,
				    size_t size)
{
	struct spi_device *spi = z2->spidev;
	u32 valid, min_dma, z2_delay, cs_delay, cpha, cpol, boot_timeout;
	u32 mode = spi->mode;
	int error;

	if (size < APPLE_Z2_FW_CONFIG_WORDS * sizeof(__le32))
		return -EINVAL;

	valid = apple_z2_fw_config_word(data, 0);
	min_dma = apple_z2_fw_config_word(data, 1);
	z2_delay = apple_z2_fw_config_word(data, 2);
	cs_delay = apple_z2_fw_config_word(data, 3);
	cpha = apple_z2_fw_config_word(data, 4);
	cpol = apple_z2_fw_config_word(data, 5);
	boot_timeout = apple_z2_fw_config_word(data, 9);
	if ((valid & APPLE_Z2_FW_CONFIG_MIN_DMA) && min_dma) {
		if (apple_z2_is_iphone7_plus(z2)) {
			if (min_dma > APPLE_Z2_RX_BUF_SIZE)
				return -EINVAL;
			z2->dma_min_len = min_dma;
		} else {
			z2->bpw16_min_len = apple_z2_is_j71b(z2) ? max_t(u32,17,min_dma) : min_dma;
		}
	}
	if (valid & APPLE_Z2_FW_CONFIG_Z2_DELAY)
		z2->z2_inter_packet_delay_us = z2_delay;
	/* iPhone 7 Plus uses zero to retain the required 1 ms CS delay. */
	if ((valid & APPLE_Z2_FW_CONFIG_CS_DELAY) &&
	    (!apple_z2_is_iphone7_plus(z2) || cs_delay))
		z2->z2_cs_delay_us = cs_delay;
	if ((valid & APPLE_Z2_FW_CONFIG_BOOT_TIMEOUT) && boot_timeout)
		z2->boot_timeout_ms = boot_timeout;

	if (valid & APPLE_Z2_FW_CONFIG_CPHA) {
		if (cpha)
			mode |= SPI_CPHA;
		else
			mode &= ~SPI_CPHA;
	}
	if (valid & APPLE_Z2_FW_CONFIG_CPOL) {
		if (cpol)
			mode |= SPI_CPOL;
		else
			mode &= ~SPI_CPOL;
	}

	if (mode != spi->mode) {
		u32 old_mode = spi->mode;

		spi->mode = mode;
		error = spi_setup(spi);
		if (error) {
			spi->mode = old_mode;
			return error;
		}
	}

	apple_z2_apply_z2_delays(z2);
	dev_dbg(&spi->dev,
		"firmware SPI config: valid=%#x dma-min-len=%u bpw16-min-len=%u z2-delay=%u cs-delay=%u boot-timeout=%u mode=%#x\n",
		 valid, z2->dma_min_len, z2->bpw16_min_len,
		 z2->z2_inter_packet_delay_us,
		 z2->z2_cs_delay_us, z2->boot_timeout_ms, spi->mode);

	return 0;
}

static int apple_z2_upload_firmware(struct apple_z2 *z2)
{
	const struct apple_z2_fw_hdr *fw_hdr;
	const struct firmware *fw = z2->validated_fw;
	size_t fw_idx = sizeof(*fw_hdr);
	size_t next_idx;
	size_t size;
	u32 load_cmd;
	u32 address;
	u32 timeout_ms;
	bool defer_irq_wait;
	bool init;
	int error;

	if (!fw) return -EINVAL;
	if (fw->size < sizeof(*fw_hdr))
		return -EINVAL;

	fw_hdr = (const struct apple_z2_fw_hdr *)fw->data;
	if (le32_to_cpu(fw_hdr->magic) != APPLE_Z2_FW_MAGIC ||
	    le32_to_cpu(fw_hdr->version) != 1)
		return dev_err_probe(&z2->spidev->dev, -EINVAL,
				     "invalid firmware header\n");

	while (fw_idx < fw->size) {
		if (fw->size - fw_idx < 2 * sizeof(__le32))
			return -EINVAL;

		load_cmd = get_unaligned_le32(fw->data + fw_idx);
		fw_idx += sizeof(__le32);
		size = get_unaligned_le32(fw->data + fw_idx);
		fw_idx += sizeof(__le32);

		switch (load_cmd) {
		case LOAD_COMMAND_INIT_PAYLOAD:
		case LOAD_COMMAND_SEND_BLOB: {
			const u8 *blob = fw->data + fw_idx;

			if (size > fw->size - fw_idx)
				return -EINVAL;
			init = load_cmd == LOAD_COMMAND_INIT_PAYLOAD;
			next_idx = round_up(fw_idx + size, 4);
			defer_irq_wait = next_idx <= fw->size &&
				fw->size - next_idx >= 2 * sizeof(__le32) &&
				get_unaligned_le32(fw->data + next_idx) ==
					LOAD_COMMAND_WAIT_IRQ;
			error = apple_z2_send_firmware_blob(z2, blob, size, init,
							    defer_irq_wait);
			if (error)
				return error;
			fw_idx += size;
			break;
		}
		case LOAD_COMMAND_RAW_XFER:
			if (size > fw->size - fw_idx)
				return -EINVAL;
			error = apple_z2_send_firmware_raw_xfer(z2, fw->data + fw_idx, size);
			if (error)
				return error;
			fw_idx += size;
			break;
		case LOAD_COMMAND_SET_CONFIG:
			if (size > fw->size - fw_idx)
				return -EINVAL;
			error = apple_z2_apply_fw_config(z2, fw->data + fw_idx, size);
			if (error)
				return error;
			fw_idx += size;
			break;
		case LOAD_COMMAND_SEND_CALIBRATION: {
			const u8 *data __free(kfree) = NULL;

			address = size;
			data = apple_z2_build_cal_blob(z2, CAL_PROP_NAME, address,
						       U32_MAX, &size, false);
			if (IS_ERR(data))
				return PTR_ERR(data);
			if (data) {
				error = apple_z2_send_firmware_blob(z2, data, size, false, false);
				if (error)
					return error;
			}
			break;
		}
		case LOAD_COMMAND_SEND_CALIBRATION_V2: {
			const u8 *data __free(kfree) = NULL;
			const char *property;
			u32 max_size;
			u32 provider;

			if (!apple_z2_is_iphone7_plus(z2))
				return -EINVAL;
			if (size != sizeof(struct apple_z2_fw_calibration) ||
			    size > fw->size - fw_idx)
				return -EINVAL;
			address = get_unaligned_le32(fw->data + fw_idx);
			max_size = get_unaligned_le32(fw->data + fw_idx + sizeof(__le32));
			provider = get_unaligned_le32(fw->data + fw_idx +
						      2 * sizeof(__le32));
			property = apple_z2_calibration_property(provider);
			if (!property || !max_size)
				return -EINVAL;
			fw_idx += size;

			data = apple_z2_build_iphone7_cal(z2, property, address,
							  max_size, &size);
			if (IS_ERR(data))
				return PTR_ERR(data);
			error = apple_z2_send_firmware_blob(z2, data, size,
							    false, false);
			if (error)
				return error;
			break;
		}
		case LOAD_COMMAND_WAIT_IRQ:
			if (size != sizeof(__le32) || size > fw->size - fw_idx)
				return -EINVAL;
			timeout_ms = get_unaligned_le32(fw->data + fw_idx);
			fw_idx += size;
			error = apple_z2_wait_ready_irq(z2, timeout_ms);
			if (error)
				return error;
			if (apple_z2_uses_ipad_protocol(z2)) {
				disable_irq(z2->spidev->irq);
				error = apple_z2_read_device_info_locked(z2);
				if (apple_z2_is_j71b(z2)) z2->upload_irq_masked=true;
				else enable_irq(z2->spidev->irq);
				if (error)
					return error;
			}
			break;
		default:
			return -EINVAL;
		}

		fw_idx = round_up(fw_idx, 4);
	}

	if (apple_z2_is_iphone7_plus(z2)) {
		msleep(50);
		mutex_lock(&z2->io_lock);
		error = apple_z2_iphone7_plus_init_locked(z2);
		if (!error) {
			z2->booted = true;
			error = apple_z2_read_packet(z2);
		}
		if (!error && apple_z2_is_d11(z2) &&
		    (!z2->runtime_frame_valid ||
		     z2->rx_buf[5] != APPLE_Z2_D11_STATUS_REPORT))
			error = -EPROTO;
		if (!error && apple_z2_is_d11(z2))
			error = apple_z2_enable_d11_reports_locked(z2);
		mutex_unlock(&z2->io_lock);
		return error;
	}

	z2->booted = true;
	mutex_lock(&z2->io_lock);
	error = apple_z2_read_packet(z2);
	if (!error && apple_z2_uses_ipad_protocol(z2) && !z2->runtime_frame_valid)
		error = -EPROTO;
	if (!error && apple_z2_uses_ipad_protocol(z2))
		error = apple_z2_advance_protocol(z2, APPLE_Z2_STATE_DEVICE_INFO,
						  APPLE_Z2_STATE_RUNTIME);
	if (!error && apple_z2_uses_ipad_protocol(z2))
		error = apple_z2_j172_init_reads_locked(z2);
	/* Metadata/first frame are now established. Drain runtime notifications
	 * during the reference settling interval, using allocated MT state; no
	 * userspace handlers attach until input_register_device succeeds later.
	 */
	if (!error && apple_z2_is_j71b(z2) && z2->upload_irq_masked) {
		z2->upload_irq_masked=false;enable_irq(z2->spidev->irq);
	}
	mutex_unlock(&z2->io_lock);
	if (error)
		return error;

	if (!apple_z2_uses_ipad_protocol(z2))
		return 0;

	msleep(2260);
	mutex_lock(&z2->io_lock);
	error = apple_z2_j172_init_writes_locked(z2);
	mutex_unlock(&z2->io_lock);
	return error;
}

static int apple_z2_apply_initial_config(struct apple_z2 *z2)
{
	const struct apple_z2_fw_hdr *fw_hdr;
	const struct firmware *fw = z2->validated_fw;
	size_t fw_idx = sizeof(*fw_hdr);
	u32 load_cmd;
	size_t size;

	if (!fw) return -EINVAL;
	if (fw->size < sizeof(*fw_hdr))
		return -EINVAL;

	fw_hdr = (const struct apple_z2_fw_hdr *)fw->data;
	if (le32_to_cpu(fw_hdr->magic) != APPLE_Z2_FW_MAGIC ||
	    le32_to_cpu(fw_hdr->version) != 1)
		return -EINVAL;

	while (fw_idx < fw->size) {
		if (fw->size - fw_idx < 2 * sizeof(__le32))
			return -EINVAL;

		load_cmd = get_unaligned_le32(fw->data + fw_idx);
		fw_idx += sizeof(__le32);
		size = get_unaligned_le32(fw->data + fw_idx);
		fw_idx += sizeof(__le32);

		if (load_cmd == LOAD_COMMAND_SEND_CALIBRATION)
			continue;
		if (size > fw->size - fw_idx)
			return -EINVAL;
		if (load_cmd == LOAD_COMMAND_SEND_CALIBRATION_V2) {
			fw_idx = round_up(fw_idx + size, 4);
			continue;
		}
		if (load_cmd == LOAD_COMMAND_SET_CONFIG)
			return apple_z2_apply_fw_config(z2,
						fw->data + fw_idx, size);
		if (load_cmd != LOAD_COMMAND_INIT_PAYLOAD &&
		    load_cmd != LOAD_COMMAND_SEND_BLOB &&
		    load_cmd != LOAD_COMMAND_RAW_XFER &&
		    load_cmd != LOAD_COMMAND_WAIT_IRQ)
			return -EINVAL;

		fw_idx = round_up(fw_idx + size, 4);
	}

	return -EINVAL;
}

static int apple_z2_boot_preamble(struct apple_z2 *z2, bool full_duplex)
{
	struct spi_transfer xfer = {
		.tx_buf = z2->tx_buf,
		.len = 4,
		.bits_per_word = 8,
		.speed_hz = apple_z2_is_j71b(z2) ? 500000 : 0,
	};
	int error;

	memset(z2->tx_buf, 0, xfer.len);
	if (full_duplex) {
		memset(z2->rx_buf, 0, xfer.len);
		xfer.rx_buf = z2->rx_buf;
	}

	apple_z2_apply_z2_delays(z2);
	error = spi_sync_transfer(z2->spidev, &xfer, 1);
	if (error)
		return error;

	return apple_z2_advance_protocol(z2, APPLE_Z2_STATE_POWERED,
					 APPLE_Z2_STATE_SPI_CONFIGURED);
}

static int apple_z2_iphone7_plus_post_boot_preamble(struct apple_z2 *z2)
{
	struct spi_transfer xfer = {
		.tx_buf = z2->tx_buf,
		.rx_buf = z2->rx_buf,
		.len = 4,
		.bits_per_word = 8,
	};

	memset(z2->tx_buf, 0, xfer.len);
	memset(z2->rx_buf, 0, xfer.len);
	apple_z2_apply_z2_delays(z2);

	return spi_sync_transfer(z2->spidev, &xfer, 1);
}

static int j71b_verified_runtime_boot(struct apple_z2 *z2)
{
 struct j71b_readiness *test=&z2->verified_boot;
 int error;
 z2->booted=false;apple_z2_reset_protocol(z2);
 test->spi=z2->spidev;test->reset=z2->reset_gpio;
 test->ana=z2->power_ana_gpio;test->ldo=z2->power_ldo_gpio;test->clk=z2->clk;
 z2->bpw16_min_len=17;z2->z2_inter_packet_delay_us=1000;z2->z2_cs_delay_us=10000;
 error=j71b_verified_transport(test,z2->tx_buf,z2->rx_buf);
 if(error)return error;
 msleep(50); /* Sandcastle bootload settling interval before report queries. */
 /* Handoff occurs with touch IRQ disabled. Cached firmware and embedded boot
  * context remain alive throughout the binding lifetime. */
 z2->clk_enabled=test->clock_on;test->clock_on=false;
 z2->platform_powered=true;z2->upload_irq_masked=true;
 z2->protocol_state=APPLE_Z2_STATE_DEVICE_INFO;z2->booted=true;
 mutex_lock(&z2->io_lock);
 error=apple_z2_read_packet(z2);
 if(!error&&!z2->runtime_frame_valid&&!z2->runtime_queue_empty)error=-EPROTO;
 if(!error)error=apple_z2_advance_protocol(z2,APPLE_Z2_STATE_DEVICE_INFO,APPLE_Z2_STATE_RUNTIME);
 if(!error)error=apple_z2_j172_init_reads_locked(z2);
 if(!error)error=j71b_drain_reports(z2);
 mutex_unlock(&z2->io_lock);
 if(error)goto fail;
 error=j71b_level_service(z2);
 if(error)goto fail;
 z2->upload_irq_masked=false;enable_irq(z2->spidev->irq);
 msleep(2260);
 mutex_lock(&z2->io_lock);error=apple_z2_j172_init_writes_locked(z2);mutex_unlock(&z2->io_lock);
 if(!error)return 0;
 fail:
 z2->booted=false;
 if(!z2->upload_irq_masked)disable_irq(z2->spidev->irq);
 z2->upload_irq_masked=true;
 gpiod_set_value_cansleep(z2->reset_gpio,1);apple_z2_platform_power_off(z2);
 return error;
}

static int apple_z2_boot(struct apple_z2 *z2)
{
	bool irq_enabled = false;
	int error;

	if (apple_z2_is_j71b(z2)) return j71b_verified_runtime_boot(z2);
	z2->booted = false;
	apple_z2_reset_protocol(z2);
	error = apple_z2_platform_power_on(z2);
	if (error)
		return error;

	if (apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) {
		error = apple_z2_apply_initial_config(z2);
		if (error)
			goto err_stop;
	}
	if (apple_z2_uses_ipad_protocol(z2)) {
		error = apple_z2_boot_preamble(z2, false);
		if (error)
			goto err_stop;
	} else if (apple_z2_is_iphone7_plus(z2)) {
		/* iPhone 7 Plus performs a legacy reset and zero preamble first. */
		error = apple_z2_pulse_reset(z2);
		if (error)
			goto err_stop;
		error = apple_z2_boot_preamble(z2, true);
		if (error)
			goto err_stop;

		/* Hold reset while arming the IRQ to discard the first boot edge. */
		gpiod_set_value_cansleep(z2->reset_gpio, 1);
		usleep_range(1000, 2000);
	}

	reinit_completion(&z2->boot_irq);
	enable_irq(z2->spidev->irq);
	irq_enabled = true;
	if (apple_z2_uses_ipad_protocol(z2)) {
		error = apple_z2_pulse_reset(z2);
	} else if (apple_z2_is_iphone7_plus(z2)) {
		gpiod_set_value_cansleep(z2->reset_gpio, 0);
		usleep_range(1000, 2000);
		error = 0;
	} else {
		gpiod_set_value_cansleep(z2->reset_gpio, 0);
		error = 0;
	}
	if (error)
		goto err_stop;

	if (!wait_for_completion_timeout(&z2->boot_irq,
					 msecs_to_jiffies(z2->boot_timeout_ms))) {
		error = -ETIMEDOUT;
		goto err_stop;
	}
	if (apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) {
		error = apple_z2_advance_protocol(z2,
						  APPLE_Z2_STATE_SPI_CONFIGURED,
						  APPLE_Z2_STATE_BOOT_IRQ);
		if (error)
			goto err_stop;
	}
	if (apple_z2_is_iphone7_plus(z2)) {
		error = apple_z2_iphone7_plus_post_boot_preamble(z2);
		if (error)
			goto err_stop;
	}

	if (apple_z2_uses_ipad_protocol(z2) || apple_z2_is_iphone7_plus(z2)) {
		disable_irq(z2->spidev->irq);
		z2->upload_irq_masked = true;
	}

	error = apple_z2_upload_firmware(z2);
	if (error)
		goto err_stop;

	return 0;

err_stop:
	z2->booted = false;
	if (z2->upload_irq_masked) {
		z2->upload_irq_masked = false;
		enable_irq(z2->spidev->irq);
	}
	if (irq_enabled)
		disable_irq(z2->spidev->irq);
	gpiod_set_value_cansleep(z2->reset_gpio, 1);
	apple_z2_platform_power_off(z2);
	apple_z2_reset_protocol(z2);
	return error;
}

static const struct apple_z2_chip_info apple_z2_j293_info = {
	.name = "MacBookPro17,1 Touch Bar",
	.variant = APPLE_Z2_VARIANT_GENERIC,
};

static const struct apple_z2_chip_info apple_z2_j493_info = {
	.name = "Mac14,7 Touch Bar",
	.variant = APPLE_Z2_VARIANT_GENERIC,
};

static const struct apple_z2_chip_info apple_z2_j71b_info = {
 .name="J71b Z2 Touchscreen (experimental)",.variant=APPLE_Z2_VARIANT_J71B,
};

static const struct apple_z2_chip_info apple_z2_j172_info = {
	.name = "iPad7,12 Touchscreen",
	.variant = APPLE_Z2_VARIANT_J172,
};

static const struct apple_z2_chip_info apple_z2_d11_info = {
	.name = "iPhone9,2 Touchscreen",
	.variant = APPLE_Z2_VARIANT_D11,
};

static const struct apple_z2_chip_info apple_z2_d111_info = {
	.name = "iPhone9,4 Touchscreen",
	.variant = APPLE_Z2_VARIANT_D111,
};

static void j71b_release_firmware(void *data){release_firmware(data);}
static int j71b_load_prevalidated_fw(struct apple_z2 *z2)
{
 const struct firmware *fw;
 int ret=request_firmware_direct(&fw,"apple/j71b-fwstart.z2fw",&z2->spidev->dev);
 if(ret)return ret;
 ret=j71b_validate_fw(fw->data,fw->size,&z2->verified_boot.plan);
 if(ret){release_firmware(fw);return ret;}
 ret=devm_add_action_or_reset(&z2->spidev->dev,j71b_release_firmware,(void *)fw);
 if(ret)return ret;
 z2->validated_fw=fw;
 dev_info(&z2->spidev->dev,"J71B_RUNTIME_PREFLIGHT_PASS records=12\n");
 return 0;
}

static int j71b_runtime_probe_body(struct spi_device *spi)
{
	struct device *dev = &spi->dev;
	const struct apple_z2_chip_info *info;
	struct apple_z2 *z2;
	unsigned int mt_flags = INPUT_MT_DIRECT;
	unsigned int slots;
	int cal_size;
	int error;

	if (!enable_runtime || !of_machine_is_compatible("apple,j71b") ||
	    !of_device_is_compatible(dev->of_node,"apple,j71b-touchscreen-readiness") ||
	    !device_property_read_bool(dev,"apple,firmware-startup-only") ||
	    spi->max_speed_hz!=4000000 || !spi->controller->dma_tx ||
	    !apple_sio_payload_allowed() ||
	    device_property_present(dev,"display-sync-gpios") ||
	    device_property_present(dev,"display-sync1-gpios")) return -EPERM;
	info=&apple_z2_j71b_info;

	z2 = devm_kzalloc(dev, sizeof(*z2), GFP_KERNEL);
	if (!z2)
		return -ENOMEM;

	z2->tx_buf = devm_kzalloc(dev, APPLE_Z2_CMD_SIZE, GFP_KERNEL);
	if (!z2->tx_buf)
		return -ENOMEM;
	z2->rx_buf = devm_kzalloc(dev, APPLE_Z2_RX_BUF_SIZE, GFP_KERNEL);
	if (!z2->rx_buf)
		return -ENOMEM;

	z2->spidev = spi;
	z2->variant = info->variant;
	error=j71b_load_prevalidated_fw(z2);
	if(error)return error;
	z2->boot_timeout_ms = 20;
	if (apple_z2_is_iphone7_plus(z2)) {
		z2->z2_inter_packet_delay_us = 1000;
		z2->z2_cs_delay_us = 1000;
		z2->boot_timeout_ms = 500;
	}
	z2->bpw16_min_len = 1;
	device_property_read_u32(dev, "apple,z2-bpw16-min-len",
				 &z2->bpw16_min_len);
	if (!z2->bpw16_min_len)
		z2->bpw16_min_len = 1;
	z2->no_init_ack = true;
	init_completion(&z2->boot_irq);
	mutex_init(&z2->io_lock);
	spi_set_drvdata(spi, z2);

	z2->reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(z2->reset_gpio))
		return dev_err_probe(dev, PTR_ERR(z2->reset_gpio),
				     "unable to get reset GPIO\n");
	if (apple_z2_is_iphone7_plus(z2)) {
		z2->hv_supply = devm_regulator_get(dev, "hv");
		if (IS_ERR(z2->hv_supply))
			return dev_err_probe(dev, PTR_ERR(z2->hv_supply),
					     "unable to get HV supply\n");

		z2->core_supply = devm_regulator_get(dev, "core");
		if (IS_ERR(z2->core_supply))
			return dev_err_probe(dev, PTR_ERR(z2->core_supply),
					     "unable to get core supply\n");
	}

	z2->power_ana_gpio =
		devm_gpiod_get_optional(dev, "power-ana", GPIOD_OUT_LOW);
	if (IS_ERR(z2->power_ana_gpio))
		return dev_err_probe(dev, PTR_ERR(z2->power_ana_gpio),
				     "unable to get analog power GPIO\n");
	z2->power_ldo_gpio =
		devm_gpiod_get_optional(dev, "power-ldo", GPIOD_OUT_LOW);
	if (IS_ERR(z2->power_ldo_gpio))
		return dev_err_probe(dev, PTR_ERR(z2->power_ldo_gpio),
				     "unable to get LDO power GPIO\n");
	if (!z2->power_ana_gpio || !z2->power_ldo_gpio) return -ENODEV;
	z2->display_sync_gpio =
		devm_gpiod_get_optional(dev, "display-sync", GPIOD_OUT_LOW);
	if (IS_ERR(z2->display_sync_gpio))
		return dev_err_probe(dev, PTR_ERR(z2->display_sync_gpio),
				     "unable to get display-sync GPIO\n");
	z2->display_sync1_gpio =
		devm_gpiod_get_optional(dev, "display-sync1", GPIOD_OUT_LOW);
	if (IS_ERR(z2->display_sync1_gpio))
		return dev_err_probe(dev, PTR_ERR(z2->display_sync1_gpio),
				     "unable to get display-sync1 GPIO\n");

	z2->clk = devm_clk_get_optional(dev, NULL);
	if (IS_ERR(z2->clk))
		return dev_err_probe(dev, PTR_ERR(z2->clk),
				     "unable to get clock\n");

	if (!z2->clk) return -ENODEV;
	error = devm_request_threaded_irq(dev, spi->irq, NULL, apple_z2_irq,
					  IRQF_ONESHOT | IRQF_NO_AUTOEN,
					  "apple-z2-irq", z2);
	if (error)
		return dev_err_probe(dev, error, "unable to request IRQ\n");

	z2->fw_name="apple/j71b-fwstart.z2fw";
	if(atomic_xchg(&attempted,1))return -EALREADY;
	if (apple_z2_is_iphone7_plus(z2)) {
		static const struct {
			const char *name;
			unsigned int max_size;
		} calibration_properties[] = {
			{ CAL_PROP_NAME, 0x708 },
			{ ORB_GAP_CAL_PROP_NAME, 0x3e0 },
			{ ORB_FORCE_CAL_PROP_NAME, 0x5c4 },
			{ SHAPE_ACCEL_CAL_PROP_NAME, 0xd0 },
		};
		unsigned int i;

		for (i = 0; i < ARRAY_SIZE(calibration_properties); i++) {
			cal_size = device_property_count_u8(dev,
							    calibration_properties[i].name);
			if (cal_size <= 0 ||
			    cal_size > calibration_properties[i].max_size ||
			    (apple_z2_is_d111(z2) &&
			     i == APPLE_Z2_CAL_MULTI_TOUCH &&
			     cal_size != APPLE_Z2_D111_CAL_SIZE))
				return dev_err_probe(dev, -EINVAL,
						     "invalid iPhone 7 Plus calibration %s size %d\n",
						     calibration_properties[i].name, cal_size);
		}
	}

	z2->input_dev = devm_input_allocate_device(dev);
	if (!z2->input_dev)
		return -ENOMEM;
	z2->input_dev->name = info->name;
	z2->input_dev->phys = "apple_z2";
	z2->input_dev->id.bustype = BUS_SPI;

	input_set_abs_params(z2->input_dev, ABS_MT_POSITION_X, 0, 1535, 0, 0);
	input_set_abs_params(z2->input_dev, ABS_MT_POSITION_Y, 0, 2047, 0, 0);
	touchscreen_parse_properties(z2->input_dev, true, &z2->props);
	input_set_abs_params(z2->input_dev, ABS_MT_WIDTH_MAJOR, 0, 65535, 0, 0);
	input_set_abs_params(z2->input_dev, ABS_MT_WIDTH_MINOR, 0, 65535, 0, 0);
	input_set_abs_params(z2->input_dev, ABS_MT_TOUCH_MAJOR, 0, 65535, 0, 0);
	input_set_abs_params(z2->input_dev, ABS_MT_TOUCH_MINOR, 0, 65535, 0, 0);
	input_set_abs_params(z2->input_dev, ABS_MT_ORIENTATION, -32768, 32767,
			     0, 0);

	if (apple_z2_uses_ipad_protocol(z2)) {
		slots = APPLE_Z2_J172_MAX_CONTACTS;
	} else if (apple_z2_is_iphone7_plus(z2)) {
		slots = 10;
		if (apple_z2_is_d11(z2))
			mt_flags |= INPUT_MT_DROP_UNUSED;
	} else {
		slots = 256;
	}
	error = input_mt_init_slots(z2->input_dev, slots, mt_flags);
	if (error)
		return dev_err_probe(dev, error,
				     "unable to initialize multitouch slots\n");
	usleep_range(5000,10000);
	error=apple_z2_boot(z2);
	if(error)return error;
	error = input_register_device(z2->input_dev);
	if (error) {
		if(!z2->upload_irq_masked)disable_irq(spi->irq);
		gpiod_set_value_cansleep(z2->reset_gpio,1);
		z2->booted=false;apple_z2_platform_power_off(z2);
		return dev_err_probe(dev,error,"unable to register input device\n");
	}
	if(z2->upload_irq_masked){z2->upload_irq_masked=false;enable_irq(spi->irq);}
	dev_info(dev,"J71B_RUNTIME_INPUT_REGISTERED metadata-ready contacts-untested\n");
	return 0;
}

static int j71b_runtime_probe(struct spi_device *spi)
{ int ret=j71b_runtime_probe_body(spi);result=ret;return ret; }

static void apple_z2_stop(struct apple_z2 *z2)
{
	disable_irq(z2->spidev->irq);
	gpiod_direction_output(z2->reset_gpio, 1);
	z2->booted = false;
	apple_z2_platform_power_off(z2);
	apple_z2_reset_protocol(z2);
}

static void apple_z2_remove(struct spi_device *spi)
{
	apple_z2_stop(spi_get_drvdata(spi));
}

static int apple_z2_suspend(struct device *dev)
{
	apple_z2_stop(spi_get_drvdata(to_spi_device(dev)));

	return 0;
}

static int apple_z2_resume(struct device *dev)
{
	struct apple_z2 *z2 = spi_get_drvdata(to_spi_device(dev));

	return apple_z2_boot(z2);
}

static DEFINE_SIMPLE_DEV_PM_OPS(apple_z2_pm, apple_z2_suspend, apple_z2_resume);

/* No matching OF entry: only explicit driver_override selects this test. */
static struct spi_device_id apple_z2_of_id[] = {
 { .name="j71b-z2-runtime", .driver_data=(kernel_ulong_t)&apple_z2_j71b_info },
 {}
};
MODULE_DEVICE_TABLE(spi, apple_z2_of_id);

static struct spi_driver apple_z2_driver = {
	.driver = {
		.name	= "j71b-z2-runtime",
		/* No automatic suspend/resume firmware reattempts in this test. */
	},
	.id_table = apple_z2_of_id,
	.probe    = j71b_runtime_probe,
	.remove   = apple_z2_remove,
};

module_spi_driver(apple_z2_driver);

MODULE_LICENSE("GPL");
MODULE_FIRMWARE("apple/j71b-fwstart.z2fw");
MODULE_DESCRIPTION("J71b experimental Z2 runtime; explicit opt-in and driver_override only");

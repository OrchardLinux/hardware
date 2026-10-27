/* SPDX-License-Identifier: GPL-2.0-only */
/* CBTL1610 detector adapted from linux-a9 charge7. The sole owner includes it.
 * policy_lock protects all state. Analog callers also hold DWC2's role lock.
 */
#ifndef J71B_SOURCE_H
#define J71B_SOURCE_H

struct charge_source {
	struct i2c_client *client;
	struct device_node *usb;
	unsigned int epoch;
	unsigned int generation;
	int allowance;
	const char *type;
	unsigned long detect_after;
	u8 route;
	bool present;
	bool computer;
	bool pm_blocked;
	bool forget_pending;
	bool sampled;
	bool route_pending;
	bool dcp_pending;
	bool adc_poisoned;
};

static struct charge_source source = { .epoch = 1, .type = "unknown" };

static int tristar_read(u8 reg)
{
	struct i2c_client *client = source.client;
	union i2c_smbus_data data;
	int ret;

	/* iOS: selector + STOP, then a separate read START. */
	i2c_lock_bus(client->adapter, I2C_LOCK_SEGMENT);
	ret = __i2c_smbus_xfer(client->adapter, client->addr, client->flags,
			       I2C_SMBUS_WRITE, reg, I2C_SMBUS_BYTE, NULL);
	if (!ret)
		ret = __i2c_smbus_xfer(client->adapter, client->addr, client->flags,
				       I2C_SMBUS_READ, 0, I2C_SMBUS_BYTE, &data);
	i2c_unlock_bus(client->adapter, I2C_LOCK_SEGMENT);
	return ret ?: data.byte;
}

static int tristar_write(u8 reg, u8 value)
{
	int ret = i2c_smbus_write_byte_data(source.client, reg, value);

	if (ret)
		return ret;
	ret = tristar_read(reg);
	return ret < 0 ? ret : ret == value ? 0 : -EUCLEAN;
}

static int source_restore_route(void)
{
	int ret;

	if (source.dcp_pending) {
		ret = tristar_write(0x03, 0);
		if (ret)
			return ret;
		source.dcp_pending = false;
	}
	if (!source.route_pending)
		return 0;
	ret = tristar_write(0x01, source.route);
	if (!ret)
		source.route_pending = false;
	return ret;
}

static int pmic_read(unsigned int reg)
{
	unsigned int value;
	int ret = regmap_read(map, reg, &value);

	return ret ?: value;
}

/* J71b 21H461: ADC start 6b85e0c, result 6b85e40, gated read 6b7c3f4.
 * 0x4000=1 is the only non-charging PMIC write. Never retry an uncertain ADC.
 */
static int source_adc_mv(void)
{
	unsigned int raw, q16;
	u8 result[2];
	int status, ret;

	if (source.adc_poisoned)
		return -EIO;
	ret = read_poll_timeout(pmic_read, status, status < 0 || !(status & 1),
				1000, 20000, false, 0x156);
	if (ret || status < 0)
		return ret ?: status;
	ret = regmap_write(map, 0x4000, 1);
	if (ret)
		goto poison;
	ret = read_poll_timeout(pmic_read, status, status < 0 || (status & 1),
				1000, 1000000, false, 0x156);
	if (ret || status < 0) {
		ret = ret ?: status;
		goto poison;
	}
	ret = regmap_bulk_read(map, 0x4001, result, sizeof(result));
	if (ret)
		goto poison;
	if (result[0] & 0x80)
		return -ENODATA;
	raw = (result[0] & 0x0f) | (result[1] << 4);
	q16 = div_u64((40ULL << 16) + raw * 5000ULL * 16, 1000);
	return (q16 * 1000U) >> 16;
poison:
	source.adc_poisoned = true;
	return ret;
}

static bool source_sample_valid(void)
{
	struct j71b_usb_source usb;

	return ready && !parked && lease_active && !source.forget_pending &&
	       time_before(jiffies, lease_deadline) &&
	       !usb_gadget_j71b_source(source.usb, &usb, false) &&
	       !usb.connected && usb.generation == source.generation;
}

static int source_pin_mv(bool dm, bool dcp)
{
	int ret, restore;
	u8 route;

	if (!source_sample_valid())
		return -EAGAIN;
	ret = tristar_read(0x01);
	if (ret < 0)
		return ret;
	source.route = ret;
	if ((ret & 7) == 1)
		route = (ret & 0xf0) | (dm ? 0x0d : 0x0c);
	else if ((ret & 0x70) == 0x10)
		route = (ret & 0x0f) | (dm ? 0xd0 : 0xc0);
	else
		return -EOPNOTSUPP;
	/* Never overwrite an unexplained BC pulse left by another owner. */
	ret = tristar_read(0x03);
	if (ret)
		return ret < 0 ? ret : -EBUSY;
	source.route_pending = true;
	ret = tristar_write(0x01, route);
	if (!ret) {
		usleep_range(5000, 6000);
		if (dcp && source_sample_valid()) {
			u8 pulse = dm ? 9 : 10;

			if ((source.route & 7) != 1)
				pulse += 2;
			source.dcp_pending = true;
			ret = tristar_write(0x03, pulse);
			if (!ret)
				msleep(40);
		}
		if (!ret)
			ret = source_sample_valid() ? source_adc_mv() : -EAGAIN;
	}
	restore = source_restore_route();
	if (restore)
		return -EUCLEAN;
	return source_sample_valid() ? ret : -EAGAIN;
}

/* Caller is inside the fixed-peripheral DWC2 role hardware critical section. */
static int source_monitor(void *unused)
{
	struct j71b_usb_source usb;
	int dp, dm, ret;
	const char *type = "apple-divider";

	mutex_lock(&policy_lock);
	if (!ready || parked) {
		ret = -EHOSTDOWN;
		goto out;
	}
	ret = source_restore_route();
	if (ret || !lease_active || !calibrated || source.computer ||
	    source.sampled || source.pm_blocked || source.forget_pending ||
	    source.adc_poisoned || !source.present ||
	    time_before(jiffies, source.detect_after) ||
	    time_after_eq(jiffies + 5 * HZ, lease_deadline))
		goto out;
	ret = usb_gadget_j71b_source(source.usb, &usb, false);
	if (ret || usb.connected)
		goto out;
	source.generation = usb.generation;
	dp = source_pin_mv(false, false);
	if (dp < 0) {
		ret = dp;
		goto retry;
	}
	dm = source_pin_mv(true, false);
	if (dm < 0) {
		ret = dm;
		goto retry;
	}
	ret = divider_current(dp, dm);
	if (ret < 0)
		goto retry;
	if (!ret) {
		dm = source_pin_mv(true, true);
		if (dm < 0) {
			ret = dm;
			goto retry;
		}
		if (dm >= 326 && dm <= 899) {
			dp = source_pin_mv(false, true);
			if (dp < 0) {
				ret = dp;
				goto retry;
			}
			type = dp < 326 ? "bc12-dcp" : "bc12-cdp";
			ret = 1500000;
		}
	}
	/* Strict VBUS and the same USB event generation bound the whole sweep. */
	dp = tristar_read(0x0c);
	if (dp < 0 || (dp & 3) != 3 || !source_sample_valid()) {
		ret = dp < 0 ? dp : -EAGAIN;
		goto retry;
	}
	source.allowance = ret;
	source.type = ret ? type : "unknown";
	source.sampled = true;
	ret = 0;
retry:
	source.detect_after = jiffies + HZ;
out:
	mutex_unlock(&policy_lock);
	return ret;
}
#endif

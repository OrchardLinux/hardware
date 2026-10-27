/* SPDX-License-Identifier: GPL-2.0-only */
/* CBTL1610 detector adapted from linux-a9 charge10. The sole owner includes it.
 * policy_lock protects all state. Analog callers also hold DWC2's role lock.
 */
#ifndef J71B_SOURCE_H
#define J71B_SOURCE_H

#define SOURCE_PRESENT BIT(7)
#define SOURCE_ORIENTATION BIT(6)
#define SOURCE_EMPTY 0
#define SOURCE_DEVICE 1
#define SOURCE_IDENTIFIED 2

struct charge_source {
	struct i2c_client *client;
	struct device_node *usb;
	unsigned int epoch;
	unsigned int generation;
	unsigned int usb_events;
	unsigned int usb_idle;
	unsigned int attempts;
	unsigned int failures;
	int status;
	int phase;
	int sample_status;
	int allowance;
	int bc_allowance;
	const char *type;
	const char *bc_type;
	const char *epoch_reason;
	const char *stage;
	const char *pin;
	int error;
	int id_error;
	int dp_mv;
	int dm_mv;
	int primary_mv;
	int secondary_mv;
	int cc_mv;
	int cc_first_mv;
	unsigned long detect_after;
	unsigned long id_after;
	unsigned long log_after;
	u8 id[6];
	u8 route;
	bool id_valid;
	bool id_poisoned;
	bool present;
	bool computer;
	bool pm_blocked;
	bool forget_pending;
	bool sampled;
	bool route_pending;
	bool dcp_pending;
	bool adc_poisoned;
};

static struct charge_source source = {
	.epoch = 1, .status = -1, .type = "unknown", .bc_type = "unknown",
	.epoch_reason = "boot", .stage = "initial", .pin = "none",
};

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

	if (!ready || parked || !lease_active || source.forget_pending ||
	    time_after_eq(jiffies, lease_deadline)) {
		source.stage = "lease-state";
		return false;
	}
	if (usb_gadget_j71b_source(source.usb, &usb, false)) {
		source.stage = "usb-state";
		return false;
	}
	if (source.computer || usb.connected || usb.active) {
		source.stage = "usb-active";
		return false;
	}
	if (usb.generation != source.generation) {
		source.stage = "usb-session-event";
		return false;
	}
	return true;
}

static int source_sample_vbus(void)
{
	int status;

	if (!source_sample_valid())
		return -EAGAIN;
	source.stage = "vbus-orientation";
	status = tristar_read(0x0c);
	if (status < 0)
		return status;
	if ((status & 3) != 3 || !(status & SOURCE_PRESENT))
		return -ENOLINK;
	if ((status ^ source.sample_status) & SOURCE_ORIENTATION)
		return -EAGAIN;
	return source_sample_valid() ? 0 : -EAGAIN;
}

/* J71b 21H461: opposite-lane CC dispatch 614c2d8, direct DX monitor 615da44.
 * An idle zero nibble is valid. Preserve the other nibble and restore zero too.
 */
static int source_pin_mv(bool dm, bool dcp, bool cc)
{
	int ret, restore, nibble;
	bool high = !!(source.sample_status & SOURCE_ORIENTATION) ^ cc;
	u8 route;

	source.pin = cc ? "cc" : dcp ? dm ? "primary" : "secondary" : dm ? "dm" : "dp";
	ret = source_sample_vbus();
	if (ret)
		return ret;
	source.stage = "route-read";
	ret = tristar_read(0x01);
	if (ret < 0)
		return ret;
	source.route = ret;
	nibble = high ? ret >> 4 : ret & 15;
	source.stage = "route-select";
	if (nibble != 0 && (nibble & 7) != 1)
		return -EOPNOTSUPP;
	route = high ? (ret & 0x0f) | (dm ? 0xd0 : 0xc0) :
		       (ret & 0xf0) | (dm ? 0x0d : 0x0c);
	ret = tristar_read(0x03);
	if (ret)
		return ret < 0 ? ret : -EBUSY;
	if (!source_sample_valid())
		return -EAGAIN;
	source.route_pending = true;
	source.stage = "route-write";
	ret = tristar_write(0x01, route);
	if (!ret) {
		usleep_range(5000, 6000);
		if (dcp && source_sample_valid()) {
			u8 pulse = (dm ? 9 : 10) + (high ? 2 : 0);

			source.dcp_pending = true;
			source.stage = "bc-pulse";
			ret = tristar_write(0x03, pulse);
			if (!ret)
				msleep(40);
		}
		if (!ret) {
			source.stage = "adc";
			ret = source_sample_valid() ? source_adc_mv() : -EAGAIN;
		}
	}
	restore = source_restore_route();
	if (restore) {
		source.stage = "restore";
		return -EUCLEAN;
	}
	if (ret < 0)
		return ret;
	restore = source_sample_vbus();
	return restore ?: ret;
}

static bool source_typec_cable(void)
{
	static const u8 id[] = { 0x10, 0x09, 0x08, 0, 0, 0 };

	return source.id_valid && !memcmp(source.id, id, sizeof(id));
}

/* J71b Type-C item 5e69690: <=659 / 660..1229 / >=1230 mV. */
static int source_cc_current(int mv)
{
	if (mv < 0 || mv > 4000)
		return -ERANGE;
	return mv >= 1230 ? 3000000 : mv >= 660 ? 1500000 : 0;
}

/* Separate STOPs: the CBTL1610 register selector is not a repeated-start read. */
static int tristar_read_block(u8 reg, u8 *bytes, unsigned int count)
{
	struct i2c_client *client = source.client;
	struct i2c_msg select = {
		.addr = client->addr, .flags = client->flags & I2C_M_TEN,
		.len = 1, .buf = &reg,
	};
	struct i2c_msg read = {
		.addr = client->addr, .flags = (client->flags & I2C_M_TEN) | I2C_M_RD,
		.len = count, .buf = bytes,
	};
	int ret;

	i2c_lock_bus(client->adapter, I2C_LOCK_SEGMENT);
	ret = __i2c_transfer(client->adapter, &select, 1);
	if (ret == 1)
		ret = __i2c_transfer(client->adapter, &read, 1);
	i2c_unlock_bus(client->adapter, I2C_LOCK_SEGMENT);
	return ret == 1 ? 0 : ret < 0 ? ret : -EIO;
}

static u8 source_id_crc(const u8 *bytes, unsigned int count)
{
	u8 crc = 0xff;
	int bit;

	while (count--) {
		crc ^= *bytes++;
		for (bit = 0; bit < 8; bit++)
			crc = (crc >> 1) ^ ((crc & 1) ? 0x8c : 0);
	}
	return crc;
}

/* First ID refines BC evidence; only a changed valid ID starts a new epoch. */
static int source_identified(const u8 *id)
{
	bool different = !source.id_valid || memcmp(source.id, id, sizeof(source.id));
	int ret = 0;

	if (source.id_valid && different)
		ret = source_new_epoch_locked("digital-id");
	memcpy(source.id, id, sizeof(source.id));
	source.id_valid = true;
	source.phase = SOURCE_IDENTIFIED;
	if (different) {
		source.sampled = false;
		source.detect_after = jiffies + msecs_to_jiffies(300);
		source.failures = 0;
	}
	return ret;
}

/* Bounded GetID only, adapted from linux-a9's validated CBTL1610 engine.
 * J71b sendIDBusCommand 6155f70 confirms reversed TX ending at 0x60 and GO
 * at 0x61. No reset/reorient, AID switch, power request, OTP or ESN operation.
 * Role hardware lock + policy_lock exclude ADC, PM and competing requests.
 */
static int source_get_id(void)
{
	static const u8 tx[] = { 0x5d, 2, 0, 0x74, 12 };
	u8 back[4], frame[8];
	int ret, event, before, after, step;

	if (source.id_poisoned)
		return -EIO;
	before = tristar_read(0x0c);
	if (before < 0)
		return before;
	if ((before & (SOURCE_PRESENT | 3)) != (SOURCE_PRESENT | 3))
		return -ENOLINK;
	ret = tristar_read(0x05);
	if (ret < 0 || (ret & BIT(3)))
		return ret < 0 ? ret : -EBUSY;
	/* Consume old events before GO; a retained valid RX frame is not fresh. */
	ret = tristar_read(0x0d);
	if (ret < 0)
		return ret;
	ret = i2c_smbus_write_byte_data(source.client, 0x60, 1);
	if (ret)
		return ret;
	ret = i2c_master_send(source.client, tx, sizeof(tx));
	if (ret != sizeof(tx))
		return ret < 0 ? ret : -EIO;
	ret = tristar_read_block(0x5d, back, sizeof(back));
	if (ret)
		return ret;
	if (memcmp(back, tx + 1, sizeof(back)))
		return -EUCLEAN;
	/* A timeout/uncertain GO must not leave a second command in flight. */
	source.id_poisoned = true;
	ret = i2c_smbus_write_byte_data(source.client, 0x61, 5);
	if (ret)
		return ret;
	for (step = 0; step <= 10; step++) {
		event = tristar_read(0x0d);
		if (event < 0)
			return event;
		if (event & (BIT(7) | BIT(6) | BIT(5) | BIT(2)))
			return -EIO;
		if ((event & 0x38) == 0x10 || (event & BIT(3)))
			break;
		if (step < 10)
			usleep_range(10000, 12000);
	}
	if (step > 10)
		return -ETIMEDOUT;
	source.id_poisoned = false;
	ret = tristar_read_block(0x20, frame, sizeof(frame));
	if (ret)
		return ret;
	if (frame[0] != 0x75 || source_id_crc(frame, 7) != frame[7])
		return -EBADMSG;
	after = tristar_read(0x0c);
	if (after < 0)
		return after;
	if ((before ^ after) & (SOURCE_PRESENT | SOURCE_ORIENTATION | 3))
		return -EAGAIN;
	return source_identified(frame + 1);
}

static void source_detection_failed(int error, bool typec)
{
	unsigned int seconds;

	if (typec) {
		source.allowance = source.bc_allowance;
		source.type = source.bc_type;
		source.sampled = false;
	}
	source.error = error;
	source.failures++;
	seconds = min(1U << min(source.failures - 1, 6U), 60U);
	source.detect_after = jiffies + seconds * HZ;
	if (!source.log_after || time_after_eq(jiffies, source.log_after)) {
		pr_warn("j71b charge detect: %s/%s error %d, attempt %u retry %us\n",
			source.pin, source.stage, error, source.attempts, seconds);
		source.log_after = jiffies + 60 * HZ;
	}
}

static void source_classify_locked(void)
{
	struct j71b_usb_source usb;
	bool typec = source_typec_cable();
	int ret, cc, attempt, cc_ua = 0;
	const char *type = "unknown";
	const char *bc_type;

	if (source.computer || (source.sampled && !typec) || source.pm_blocked ||
	    source.forget_pending || source.adc_poisoned || !source.present ||
	    time_before(jiffies, source.detect_after) ||
	    time_after_eq(jiffies + 9 * HZ, lease_deadline))
		return;
	ret = usb_gadget_j71b_source(source.usb, &usb, false);
	if (ret || usb.connected || usb.active)
		return;
	source.generation = usb.generation;
	source.sample_status = source.status;
	source.attempts++;
	source.dp_mv = -ENODATA;
	source.dm_mv = -ENODATA;
	source.cc_mv = -ENODATA;
	if (typec) {
		cc_ua = -1;
		for (attempt = 0; attempt < 3; attempt++) {
			cc = source_pin_mv(false, false, true);
			source.cc_mv = cc;
			if (!attempt)
				source.cc_first_mv = cc;
			if (cc < 0) {
				ret = cc;
				goto fail;
			}
			ret = source_cc_current(cc);
			if (ret < 0)
				goto fail;
			if (ret == cc_ua)
				break;
			cc_ua = ret;
			if (attempt < 2)
				msleep(20);
		}
		if (attempt == 3) {
			source.stage = "cc-debounce";
			ret = -EAGAIN;
			goto fail;
		}
		if (source.bc_allowance) {
			ret = source.bc_allowance;
			type = source.bc_type;
			goto combine;
		}
	}
	source.dp_mv = source_pin_mv(false, false, false);
	if (source.dp_mv < 0) {
		ret = source.dp_mv;
		goto fail;
	}
	source.dm_mv = source_pin_mv(true, false, false);
	if (source.dm_mv < 0) {
		ret = source.dm_mv;
		goto fail;
	}
	ret = divider_current(source.dp_mv, source.dm_mv);
	if (ret < 0)
		goto fail;
	if (ret) {
		type = "apple-divider";
	} else {
		source.primary_mv = source_pin_mv(true, true, false);
		if (source.primary_mv < 0) {
			ret = source.primary_mv;
			goto fail;
		}
		if (source.primary_mv >= 326 && source.primary_mv <= 899) {
			source.secondary_mv = source_pin_mv(false, true, false);
			if (source.secondary_mv < 0) {
				ret = source.secondary_mv;
				goto fail;
			}
			/* J71b 5e68c48 + names at 6dc3750: 2=CDP, 1=DCP. */
			type = source.secondary_mv < 326 ? "bc12-cdp" : "bc12-dcp";
			ret = 1500000;
		}
	}
combine:
	bc_type = type;
	if (cc_ua == 3000000 && strcmp(type, "bc12-dcp") && strcmp(type, "bc12-cdp"))
		cc_ua = 1500000;
	if (cc_ua && cc_ua >= ret) {
		ret = cc_ua;
		type = "usb-c";
	}
	cc = source_sample_vbus();
	if (cc) {
		ret = cc;
		goto fail;
	}
	if (!strcmp(bc_type, "bc12-dcp") || !strcmp(bc_type, "bc12-cdp")) {
		source.bc_allowance = 1500000;
		source.bc_type = bc_type;
	}
	source.allowance = ret;
	source.type = type;
	source.sampled = true;
	if (ret)
		source.phase = SOURCE_IDENTIFIED;
	source.error = 0;
	source.failures = 0;
	source.stage = "complete";
	source.detect_after = jiffies + 5 * HZ;
	return;
fail:
	source_detection_failed(ret, typec);
}

/* Caller holds fixed-peripheral DWC2 role hardware lock, then policy_lock. */
static int source_monitor(void *unused)
{
	int ret;

	mutex_lock(&policy_lock);
	if (!ready || parked) {
		ret = -EHOSTDOWN;
		goto out;
	}
	ret = source_restore_route();
	if (ret || !lease_active || !calibrated)
		goto out;
	ret = update_source_locked();
	if (ret || !lease_active)
		goto out;
	source_classify_locked();
	if (lease_active && time_before(jiffies + HZ, lease_deadline) &&
	    time_after_eq(jiffies, source.id_after)) {
		source.id_error = source_get_id();
		source.id_after = jiffies + 5 * HZ;
	}
	ret = source.route_pending || source.dcp_pending ? -EUCLEAN : 0;
out:
	mutex_unlock(&policy_lock);
	return ret;
}
#endif

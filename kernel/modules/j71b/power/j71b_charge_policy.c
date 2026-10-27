// SPDX-License-Identifier: GPL-2.0-only
/* J71b root-only, 15-second charging lease with PM-safe verified handoff.
 * Default: unchanged <=425 mA lease. Source-aware policy is explicitly opt-in.
 * Source budgets are separate from USB enumeration's current accounting.
 */
#include <linux/bits.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/firmware.h>
#include <linux/i2c.h>
#include <linux/iopoll.h>
#include <linux/jiffies.h>
#include <linux/math64.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <linux/suspend.h>
#include <linux/unaligned.h>
#include <linux/usb/j71b-source.h>
#include <linux/workqueue.h>

#define STATUS_REG 0x18a
#define INPUT_CONTROL_REG 0xa00
#define INPUT_LIMIT_REG 0xa03
#define CHARGE_CURRENT_REG 0xa08
#define MAX_CHARGE_REG 0xa0c
#define HANDOFF_LIMIT 0x02
#define SAFE_USB_LIMIT 0x1c /* D2365 nominal encoding: 425 mA */
#define OBSERVED_MAX_CHARGE 0xf4
#define LEASE_SECONDS 15

static DEFINE_MUTEX(policy_lock);
/* Serialize root setters across synchronous cancellation outside policy_lock. */
static DEFINE_MUTEX(renewal_lock);
static struct device *pmic;
static struct regmap *map;
static bool ready;
static bool changed;
static bool parked;
static bool restore_failed;
static struct power_supply *input_supply;
static bool source_aware;
static bool calibrated;
static bool source_ready;
static bool role_safe;
static bool lease_active;
static bool charge_saved;
static unsigned long lease_deadline;
static unsigned long step_after;
static unsigned long adaptive_after;
static unsigned int nominal_ua;
static unsigned int platform_ua;
static unsigned int adaptive_ua;
static unsigned int backoffs;
static unsigned int old_charge;
static unsigned int old_suspend;
static unsigned int expected_charge;
static unsigned int expected_input = HANDOFF_LIMIT;
static unsigned int adaptive_input = 0xff;
static unsigned int tristar_revision;
static int source_error;
static int policy_error;
static u8 input_codes[4];
static int restore_route(void);

module_param(source_aware, bool, 0444);
MODULE_PARM_DESC(source_aware,
		 "Opt in to source-aware charging with per-unit J71b CBAT (default: off)");

#include "j71b-charge-math.h"
#include "j71b-source.h"

/* Read-only physical input presence, using the SAME known, non-acknowledging
 * status register already read by this policy. No UDC/carrier inference and
 * no source-capacity claim. Serialize with the existing PM/lease owner.
 */
static int input_get_property(struct power_supply *psy,
		enum power_supply_property prop, union power_supply_propval *val)
{
	unsigned int status;
	int ret;

	if (prop != POWER_SUPPLY_PROP_ONLINE)
		return -EINVAL;
	mutex_lock(&policy_lock);
	if (!ready || parked || !map) {
		ret = -ENODATA;
		goto out;
	}
	ret = regmap_read(map, STATUS_REG, &status);
	if (ret)
		goto out;
	if (!status)
		val->intval = 0;
	else if (!(status & BIT(4)) && (status & GENMASK(1, 0)) &&
		 (status & 0xc0) == 0x40)
		val->intval = 1;
	else
		ret = -ENODATA;
out:
	mutex_unlock(&policy_lock);
	return ret;
}

static const enum power_supply_property input_properties[] = {
	POWER_SUPPLY_PROP_ONLINE,
};
static const struct power_supply_desc input_desc = {
	.name = "j71b-input",
	.type = POWER_SUPPLY_TYPE_USB,
	.properties = input_properties,
	.num_properties = ARRAY_SIZE(input_properties),
	.get_property = input_get_property,
};
module_param_named(sleep_parked, parked, bool, 0444);
module_param_named(restore_failed, restore_failed, bool, 0444);

static int write_checked(unsigned int reg, unsigned int value)
{
	unsigned int readback;
	int ret = regmap_write(map, reg, value);

	if (!ret) {
		ret = regmap_read(map, reg, &readback);
		if (!ret && value != readback)
			ret = -EUCLEAN;
	}
	return ret;
}

static int verify_handoff_locked(void)
{
	unsigned int value;
	int ret = regmap_read(map, INPUT_LIMIT_REG, &value);

	if (!ret && value != HANDOFF_LIMIT)
		ret = -EPERM;
	if (!ret && source_ready) {
		ret = regmap_read(map, CHARGE_CURRENT_REG, &value);
		if (!ret && value != old_charge)
			ret = -EPERM;
		if (!ret)
			ret = regmap_read(map, INPUT_CONTROL_REG, &value);
		if (!ret && (value & BIT(1)) != old_suspend)
			ret = -EPERM;
	}
	return ret;
}

static int restore_locked(void)
{
	int ret = 0, charge_ret, i;

	lease_active = false;
	nominal_ua = 0;
	if (!changed && !charge_saved)
		return 0;
	for (i = 0; i < 3; i++) {
		/* Reduce input first on handoff; never interrupt input via 0xa00. */
		ret = write_checked(INPUT_LIMIT_REG, HANDOFF_LIMIT);
		charge_ret = charge_saved ?
			write_checked(CHARGE_CURRENT_REG, old_charge) : 0;
		ret = ret ?: charge_ret;
		if (!ret) {
			changed = false;
			charge_saved = false;
			expected_input = HANDOFF_LIMIT;
			pr_info("j71b charge lease: handoff restored and verified\n");
			return 0;
		}
		msleep(100);
	}
	restore_failed = true;
	pr_crit("j71b charge lease: handoff restore unverified (%d)\n", ret);
	return ret;
}

static void lease_expired(struct work_struct *work)
{
	bool expired = false;
	int ret;

	mutex_lock(&policy_lock);
	if (ready && lease_active && time_before(jiffies, lease_deadline)) {
		mod_delayed_work(system_unbound_wq, to_delayed_work(work),
				 lease_deadline - jiffies);
	} else if (ready) {
		lease_active = false;
		expired = true;
	}
	mutex_unlock(&policy_lock);
	if (!expired)
		return;
	ret = restore_route();
	mutex_lock(&policy_lock);
	/* A root renewal may have won while we acquired the role lock. */
	if (ready && (!lease_active || time_after_eq(jiffies, lease_deadline))) {
		if (ret)
			restore_failed = true;
		restore_locked();
	}
	mutex_unlock(&policy_lock);
}

static DECLARE_DELAYED_WORK(lease_work, lease_expired);

static int check_battery(void)
{
	struct power_supply *battery;
	union power_supply_propval value;
	int ret;

	battery = power_supply_get_by_name("battery");
	if (!battery)
		return -ENODEV;
	ret = power_supply_get_property(battery, POWER_SUPPLY_PROP_PRESENT, &value);
	if (ret || value.intval != 1) goto bad;
	ret = power_supply_get_property(battery, POWER_SUPPLY_PROP_CAPACITY, &value);
	if (ret || value.intval < 15 || value.intval > 95) goto bad;
	ret = power_supply_get_property(battery, POWER_SUPPLY_PROP_TEMP, &value);
	if (ret || value.intval < 50 || value.intval > 400) goto bad;
	ret = power_supply_get_property(battery, POWER_SUPPLY_PROP_VOLTAGE_NOW, &value);
	if (ret || value.intval < 3200000 || value.intval > 4350000) goto bad;
	ret = power_supply_get_property(battery, POWER_SUPPLY_PROP_CURRENT_NOW, &value);
	if (ret || value.intval < -3000000 || value.intval > 3000000) goto bad;
	power_supply_put(battery);
	return 0;
bad:
	power_supply_put(battery);
	return ret ?: -ERANGE;
}

/* A failed read/USB reset is not a measured removal. A single observed empty
 * VBUS interval is, even if reattach happens between two policy ticks.
 */
static int update_source_locked(void)
{
	struct j71b_usb_source usb;
	int ret, vbus;

	ret = tristar_read(0x0f);
	if (ret != 0x8a)
		return ret < 0 ? ret : -ENODEV;
	vbus = tristar_read(0x0c);
	if (vbus < 0)
		return vbus;
	if (source.present && !(vbus & 3)) {
		source.present = false;
		source.epoch++;
		source.computer = false;
		source.pm_blocked = false;
		source.sampled = false;
		source.allowance = 0;
		source.type = "unknown";
		source.forget_pending = true;
		source.detect_after = jiffies + HZ;
		adaptive_ua = 0;
		adaptive_input = 0xff;
		adaptive_after = 0;
		step_after = 0;
		backoffs = 0;
		/* A new root renewal is required; work cannot replay the lease. */
		ret = restore_locked();
		if (ret)
			return ret;
	}
	if (source.forget_pending) {
		ret = usb_gadget_j71b_source(source.usb, &usb, true);
		if (ret)
			return ret;
		source.forget_pending = false;
	}
	if ((vbus & 3) != 3)
		return -ENOLINK;
	source.present = true;
	ret = usb_gadget_j71b_source(source.usb, &usb, false);
	if (ret)
		return ret;
	/* BC sources retain their proven classification when a link appears. */
	if (usb.connected && (!source.allowance ||
			      !strcmp(source.type, "apple-divider"))) {
		source.computer = true;
		source.pm_blocked = false;
		source.allowance = platform_ua;
		source.type = "computer";
	}
	return 0;
}

static int baseline(unsigned int *input)
{
	unsigned int state, cap;
	int ret = regmap_read(map, STATUS_REG, &state);

	if (!ret)
		ret = regmap_read(map, MAX_CHARGE_REG, &cap);
	if (!ret)
		ret = regmap_read(map, INPUT_LIMIT_REG, input);
	if (ret)
		return ret;
	if ((state & BIT(4)) || !(state & GENMASK(1, 0)) ||
	    (state & 0xc0) != 0x40 || cap != OBSERVED_MAX_CHARGE)
		return -EPERM;
	if (source_ready && source.allowance && (state & BIT(2)))
		return -EBUSY;
	return 0;
}

static int owned_fields_locked(void)
{
	unsigned int value;
	int ret = regmap_read(map, CHARGE_CURRENT_REG, &value);

	if (!ret && value != expected_charge)
		ret = -EUCLEAN;
	if (!ret)
		ret = regmap_read(map, INPUT_CONTROL_REG, &value);
	if (!ret && (value & BIT(1)) != old_suspend)
		ret = -EUCLEAN;
	return ret;
}

/* Never adopt unexplained elevated settings. Only an owned input reduction,
 * with all other fields matching, is accepted as a reason to lower the cap.
 * Its cause is unknown: D2365 iOS reports NO input limiting, not droop AICL.
 */
static int policy_tick_locked(void)
{
	unsigned int input, target, code = SAFE_USB_LIMIT, charge = 0;
	int ret, index, previous;

	ret = check_battery();
	if (ret)
		return ret;
	ret = baseline(&input);
	if (ret)
		return ret;
	if (!changed && input != HANDOFF_LIMIT)
		return -EUCLEAN;
	if (source_ready) {
		if (!charge_saved)
			expected_charge = old_charge;
		ret = owned_fields_locked();
		if (ret)
			return ret;
	}
	if (charge_saved) {
		ret = owned_fields_locked();
		if (ret)
			return ret;
		if (input > expected_input)
			return -EUCLEAN;
		if (input < expected_input) {
			index = max(source_step(nominal_ua) - 1, 0);
			while (index > 0 && input_codes[index] > input)
				index--;
			adaptive_ua = source_steps[index];
			adaptive_input = min_t(unsigned int, input_codes[index], input);
			adaptive_after = jiffies + 60 * HZ;
			backoffs++;
			pr_warn_ratelimited("j71b charge: observed PMIC reduction to 0x%x; hold 60s, cause unknown\n",
					    input);
		}
	} else if (changed && input != expected_input && input != HANDOFF_LIMIT) {
		return -EUCLEAN;
	}
	target = 425000;
	if (source_ready && calibrated) {
		ret = update_source_locked();
		if (ret || !lease_active)
			return ret ?: -ENOLINK;
		if (source.allowance && role_safe)
			target = min_t(unsigned int, source.allowance, platform_ua);
	}
	if (adaptive_ua && time_after_eq(jiffies, adaptive_after)) {
		index = source_step(adaptive_ua);
		if (index + 1 < ARRAY_SIZE(source_steps))
			adaptive_ua = source_steps[index + 1];
		adaptive_input = 0xff;
		adaptive_after = jiffies + 60 * HZ;
	}
	if (adaptive_ua)
		target = min(target, adaptive_ua);
	index = source_step(target);
	if (index >= 0) {
		previous = source_step(nominal_ua);
		/* 425 -> 500 -> 1000 -> 2100 -> 2400, <=one rung/second. */
		if (previous < index) {
			if (time_before(jiffies, step_after))
				index = previous;
			else
				index = min(index, previous + 1);
		}
	}
	if (index >= 0) {
		target = source_steps[index];
		code = min_t(unsigned int, input_codes[index], adaptive_input);
		charge = index ? 50 : 8; /* <=2500 mA, cap 0xf4[5:0] = 2600 mA */
		/* Save recovery intent before any write. Handoff was read at load. */
		charge_saved = true;
		/* iOS order for nonzero budget changes. No 0xa00 transaction lock. */
		if (charge != expected_charge) {
			ret = write_checked(CHARGE_CURRENT_REG, charge);
			if (ret)
				return ret;
			expected_charge = charge;
		}
	} else {
		target = 425000;
	}
	changed = true;
	if (input != code) {
		ret = write_checked(INPUT_LIMIT_REG, code);
		if (ret)
			return ret;
	}
	expected_input = code;
	if (target != nominal_ua) {
		step_after = jiffies + HZ;
		pr_info("j71b charge: %s, nominal %u mA, input 0x%x verified\n",
			source_aware ? source.type : "conservative", target / 1000, code);
	}
	nominal_ua = target;
	return 0;
}

static void stop_locked(int error)
{
	policy_error = error;
	if (error == -EUCLEAN)
		restore_failed = true;
	restore_locked();
}

static void source_poll(struct work_struct *work)
{
	bool run;
	int ret;

	mutex_lock(&policy_lock);
	run = ready && !parked;
	if (run) {
		source_error = update_source_locked();
		if (source_error && lease_active)
			stop_locked(source_error);
	}
	mutex_unlock(&policy_lock);
	if (!run)
		return;
	/* Never acquire the role hardware lock while holding policy_lock. */
	ret = dwc2_j71b_source_monitor(source.usb, source_monitor, NULL);
	mutex_lock(&policy_lock);
	if (ready && !parked) {
		if (!ret) {
			role_safe = true;
		} else if (ret == -EOPNOTSUPP || ret == -ESHUTDOWN || ret == -ENODEV) {
			role_safe = false;
			if (lease_active && charge_saved)
				stop_locked(ret);
		}
		if (ret == -EUCLEAN || source.route_pending || source.dcp_pending) {
			restore_failed = true;
			stop_locked(ret ?: -EUCLEAN);
		} else if (lease_active) {
			if (time_after_eq(jiffies, lease_deadline)) {
				restore_locked();
			} else if (!restore_failed) {
				ret = policy_tick_locked();
				if (ret)
					stop_locked(ret);
			}
		}
		mod_delayed_work(system_unbound_wq, to_delayed_work(work), HZ / 4);
	}
	mutex_unlock(&policy_lock);
}

static DECLARE_DELAYED_WORK(source_work, source_poll);

static int repair_route(void *unused)
{
	int ret;

	mutex_lock(&policy_lock);
	ret = source_restore_route();
	mutex_unlock(&policy_lock);
	return ret;
}

/* Work has been cancelled or this is a serialized root setter. */
static int restore_route(void)
{
	bool pending;

	mutex_lock(&policy_lock);
	pending = source.route_pending || source.dcp_pending;
	mutex_unlock(&policy_lock);
	return pending ? dwc2_j71b_source_monitor(source.usb, repair_route, NULL) : 0;
}

static int set_lease(const char *val, const struct kernel_param *kp)
{
	bool on;
	int ret, route_ret;

	ret = kstrtobool(val, &on);
	if (ret)
		return ret;
	if (!READ_ONCE(ready))
		return -ENODEV;
	mutex_lock(&renewal_lock);
	if (!on) {
		mutex_lock(&policy_lock);
		if (parked) {
			ret = -EBUSY;
			goto out;
		}
		lease_active = false;
		mutex_unlock(&policy_lock);
		cancel_delayed_work_sync(&lease_work);
		route_ret = restore_route();
		mutex_lock(&policy_lock);
		ret = restore_locked();
		if (!ret)
			ret = verify_handoff_locked();
		ret = ret ?: route_ret;
		restore_failed = !!ret;
		goto out;
	}
	mutex_lock(&policy_lock);
	if (parked || restore_failed) {
		ret = -EBUSY;
		goto out;
	}
	lease_active = true;
	lease_deadline = jiffies + LEASE_SECONDS * HZ;
	mod_delayed_work(system_unbound_wq, &lease_work, LEASE_SECONDS * HZ);
	ret = policy_tick_locked();
	if (ret)
		stop_locked(ret);
	else
		policy_error = 0;
out:
	mutex_unlock(&policy_lock);
	mutex_unlock(&renewal_lock);
	return ret;
}

static int policy_pm(struct notifier_block *nb, unsigned long event, void *unused)
{
	int ret, route_ret;

	switch (event) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		/* Include zero's cancellation/repair window in PM exclusion. */
		mutex_lock(&renewal_lock);
		mutex_lock(&policy_lock);
		parked = true;
		lease_active = false;
		mutex_unlock(&policy_lock);
		cancel_delayed_work_sync(&lease_work);
		cancel_delayed_work_sync(&source_work);
		route_ret = restore_route();
		mutex_lock(&policy_lock);
		ret = restore_locked();
		if (!ret)
			ret = verify_handoff_locked();
		ret = ret ?: route_ret;
		restore_failed = !!ret;
		if (source_ready) {
			struct j71b_usb_source usb;

			/* Sleep can hide a complete source swap. Forget old identity;
			 * until a measured empty interval or a new USB event, no ADC.
			 */
			source.computer = false;
			source.sampled = false;
			source.allowance = 0;
			source.type = "unknown";
			source.pm_blocked = true;
			role_safe = false;
			source.forget_pending =
				!!usb_gadget_j71b_source(source.usb, &usb, true);
		}
		if (ret) {
			/* The failing notifier receives no robust rollback callback. */
			parked = false;
			pr_err("j71b charge: suspend refused, handoff/route unverified (%d)\n",
			       ret);
			if (source_ready)
				mod_delayed_work(system_unbound_wq, &source_work, HZ);
		}
		mutex_unlock(&policy_lock);
		mutex_unlock(&renewal_lock);
		return notifier_from_errno(ret);
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		mutex_lock(&policy_lock);
		parked = false;
		/* Retain the physical epoch, but require a fresh analog sample. */
		if (!source.computer) {
			source.sampled = false;
			source.allowance = 0;
			source.type = "unknown";
		}
		source.detect_after = jiffies + 2 * HZ;
		if (source_ready)
			mod_delayed_work(system_unbound_wq, &source_work, HZ);
		mutex_unlock(&policy_lock);
		/* No register write and no lease replay. */
		return NOTIFY_OK;
	default:
		return NOTIFY_DONE;
	}
}
static struct notifier_block policy_pm_nb = { .notifier_call = policy_pm };

static int source_state_get(char *buf, const struct kernel_param *kp)
{
	unsigned int input = 0, charge = 0;
	int ret, len;

	mutex_lock(&policy_lock);
	ret = !ready || parked ? -ENODATA :
		regmap_read(map, INPUT_LIMIT_REG, &input);
	if (!ret)
		ret = regmap_read(map, CHARGE_CURRENT_REG, &charge);
	len = scnprintf(buf, PAGE_SIZE,
			"opt_in=%u calibrated=%u revision=0x%x epoch=%u source=%s allowance_ua=%d platform_ua=%u nominal_ua=%u input_code=0x%x battery_code=0x%x sample_valid=%u adaptive_ua=%u backoffs=%u last_backoff=%s vbus_mv=unavailable source_error=%d policy_error=%d live_error=%d lease_active=%u route_pending=%u dcp_pending=%u adc_poisoned=%u role_safe=%u pm_blocked=%u\n",
		source_aware, calibrated, tristar_revision, source.epoch,
		source.type, source.allowance, platform_ua, nominal_ua, input,
		charge, source.sampled, adaptive_ua, backoffs,
		backoffs ? "pmic-input-reduced" : "none", source_error,
		policy_error, ret, lease_active, source.route_pending,
		source.dcp_pending, source.adc_poisoned, role_safe, source.pm_blocked);
	mutex_unlock(&policy_lock);
	return len;
}

static const struct kernel_param_ops state_ops = { .get = source_state_get };
module_param_cb(source_state, &state_ops, NULL, 0444);

static const struct kernel_param_ops lease_ops = { .set = set_lease };
module_param_cb(lease, &lease_ops, NULL, 0200);
MODULE_PARM_DESC(lease, "Root-only 15s charger lease: 1 renews, 0 restores");

static void put_source(void)
{
	if (source.client)
		put_device(&source.client->dev);
	of_node_put(source.usb);
}

static int init_source(void)
{
	const struct firmware *fw;
	struct device_node *node;
	struct j71b_usb_source usb;
	int ret, i;

	node = of_parse_phandle(pmic->of_node, "apple,charging-source", 0);
	if (!node)
		return -ENODEV;
	if (!of_device_is_compatible(node, "apple,j71b-cbtl1610-source")) {
		of_node_put(node);
		return -EINVAL;
	}
	source.client = of_find_i2c_device_by_node(node);
	source.usb = of_parse_phandle(node, "usb-controller", 0);
	ret = of_property_read_u32(node,
				   "apple,usb-input-limit-max-microamp", &platform_ua);
	of_node_put(node);
	if (!source.client || !source.usb)
		return -ENODEV;
	if (!of_device_is_compatible(source.usb, "apple,t8010-dwc2") ||
	    of_property_match_string(source.usb, "dr_mode", "peripheral") != 0 ||
	    of_property_read_bool(source.usb, "usb-role-switch"))
		return -EOPNOTSUPP;
	if (source.client->addr != 0x1a || source.client->adapter !=
	    to_i2c_client(pmic)->adapter || source.client->dev.driver)
		return -EBUSY;
	if (ret || platform_ua < 500000 || platform_ua > 2400000)
		return -EINVAL;
	if (!i2c_check_functionality(source.client->adapter,
				     I2C_FUNC_SMBUS_BYTE | I2C_FUNC_SMBUS_BYTE_DATA))
		return -EOPNOTSUPP;
	ret = tristar_read(0x0f);
	if (ret < 0)
		return ret;
	tristar_revision = ret;
	/* Public J71b ADT proves the model, not this unit's silicon revision. */
	if (tristar_revision != 0x8a)
		return -ENODEV;
	ret = request_firmware_direct(&fw, "apple/j71b-CBAT.bin", pmic);
	if (ret)
		return ret;
	for (i = 0; i < ARRAY_SIZE(source_steps); i++) {
		ret = calibrate_input(fw->data, fw->size, source_steps[i] / 1000,
				      &input_codes[i]);
		if (ret || input_codes[i] <= HANDOFF_LIMIT ||
		    (i && input_codes[i] <= input_codes[i - 1])) {
			ret = ret ?: -ERANGE;
			break;
		}
	}
	release_firmware(fw);
	if (ret)
		return ret;
	ret = regmap_read(map, CHARGE_CURRENT_REG, &old_charge);
	if (!ret)
		ret = regmap_read(map, INPUT_CONTROL_REG, &old_suspend);
	if (ret)
		return ret;
	old_suspend &= BIT(1);
	if (old_suspend)
		return -EPERM;
	/* Start a new observation window. A preexisting configured link must
	 * not be diverted to the ADC or used as stale source evidence.
	 */
	ret = usb_gadget_j71b_source(source.usb, &usb, true);
	if (ret)
		return ret;
	source.pm_blocked = usb.active;
	expected_charge = old_charge;
	calibrated = true;
	source.detect_after = jiffies + 2 * HZ;
	return 0;
}

static int __init policy_init(void)
{
	struct power_supply *other;
	unsigned int input, max_charge;
	int ret;

	if (!of_machine_is_compatible("apple,j71b"))
		return -ENODEV;
	pmic = bus_find_device_by_name(&i2c_bus_type, NULL, "0-003c");
	if (!pmic)
		return -ENODEV;
	if (!pmic->of_node || !of_device_is_compatible(pmic->of_node,
						      "apple,angel-pmic")) {
		ret = -ENODEV;
		goto out;
	}
	map = dev_get_regmap(pmic, NULL);
	if (!map) {
		ret = -ENODEV;
		goto out;
	}
	other = power_supply_get_by_name("j71b-charger");
	if (other) {
		power_supply_put(other);
		ret = -EBUSY;
		goto out;
	}
	ret = regmap_read(map, MAX_CHARGE_REG, &max_charge);
	if (!ret)
		ret = regmap_read(map, INPUT_LIMIT_REG, &input);
	if (ret)
		goto out;
	if (input != HANDOFF_LIMIT || max_charge != OBSERVED_MAX_CHARGE) {
		ret = -EPERM;
		goto out;
	}
	if (source_aware) {
		source_error = init_source();
		source_ready = !source_error;
		if (source_error)
			pr_warn("j71b charge: source policy unavailable (%d), retaining 425mA lease\n",
				source_error);
	}
	ret = register_pm_notifier(&policy_pm_nb);
	if (ret)
		goto out;
	WRITE_ONCE(ready, true);
	input_supply = power_supply_register(pmic, &input_desc, NULL);
	if (IS_ERR(input_supply)) {
		ret = PTR_ERR(input_supply);
		input_supply = NULL;
		WRITE_ONCE(ready, false);
		unregister_pm_notifier(&policy_pm_nb);
		goto out;
	}
	if (source_ready)
		mod_delayed_work(system_unbound_wq, &source_work, HZ);
	pr_info("j71b charge lease: ready, opt-in %u; no register write at load\n",
		source_aware);
	return 0;
out:
	put_source();
	put_device(pmic);
	pmic = NULL;
	map = NULL;
	return ret;
}

static void __exit policy_exit(void)
{
	int ret;

	mutex_lock(&renewal_lock);
	mutex_lock(&policy_lock);
	ready = false;
	parked = true;
	lease_active = false;
	mutex_unlock(&policy_lock);
	mutex_unlock(&renewal_lock);
	unregister_pm_notifier(&policy_pm_nb);
	cancel_delayed_work_sync(&lease_work);
	cancel_delayed_work_sync(&source_work);
	ret = restore_route();
	if (ret)
		pr_crit("j71b charge: Tristar route restore unverified on exit (%d)\n", ret);
	mutex_lock(&policy_lock);
	restore_locked();
	mutex_unlock(&policy_lock);
	power_supply_unregister(input_supply);
	put_source();
	put_device(pmic);
}
module_init(policy_init);
module_exit(policy_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J71b guarded charge lease with opt-in source-aware policy");

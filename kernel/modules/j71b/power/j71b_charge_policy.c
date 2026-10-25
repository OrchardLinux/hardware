// SPDX-License-Identifier: GPL-2.0-only
/* J71b conservative charging lease.
 * Root-only renewal permits at most 425mA for 15 seconds. UDC/carrier are
 * NOT source-capability evidence. Every renewal rechecks PMIC and battery.
 * PM notifiers park renewals and synchronously restore/verify the 100mA
 * handoff before device suspension; unsafe restoration explicitly aborts PM.
 * Validate charging and suspend behavior on the exact kernel/device bundle.
 */
#include <linux/bits.h>
#include <linux/delay.h>
#include <linux/device.h>
#include <linux/err.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/power_supply.h>
#include <linux/regmap.h>
#include <linux/suspend.h>
#include <linux/workqueue.h>

#define STATUS_REG 0x18a
#define INPUT_LIMIT_REG 0xa03
#define MAX_CHARGE_REG 0xa0c
#define HANDOFF_LIMIT 0x02
#define SAFE_USB_LIMIT 0x1c /* Related D2365 mapping: 425mA < USB2 500mA */
#define OBSERVED_MAX_CHARGE 0xf4
#define LEASE_SECONDS 15

static DEFINE_MUTEX(policy_lock);
static struct device *pmic;
static struct regmap *map;
static bool ready;
static bool changed;
static bool parked;
static bool restore_failed;
static struct power_supply *input_supply;

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

static int restore_locked(void)
{
	unsigned int value;
	int ret = -EIO, i;

	if (!changed)
		return 0;
	for (i = 0; i < 3; i++) {
		ret = regmap_write(map, INPUT_LIMIT_REG, HANDOFF_LIMIT);
		if (!ret) {
			ret = regmap_read(map, INPUT_LIMIT_REG, &value);
			if (!ret && value == HANDOFF_LIMIT) {
				changed = false;
				pr_info("j71b charge lease: handoff input limit restored\n");
				return 0;
			}
			ret = ret ?: -EIO;
		}
		msleep(100);
	}
	pr_crit("j71b charge lease: restore unverified (%d); limit remains at most 425mA\n",
		ret);
	return ret;
}

static void lease_expired(struct work_struct *work)
{
	mutex_lock(&policy_lock);
	if (ready)
		restore_locked();
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

static int baseline(unsigned int *limit)
{
	unsigned int state, max_charge;
	int ret;

	ret = regmap_read(map, STATUS_REG, &state);
	if (ret) return ret;
	ret = regmap_read(map, MAX_CHARGE_REG, &max_charge);
	if (ret) return ret;
	ret = regmap_read(map, INPUT_LIMIT_REG, limit);
	if (ret) return ret;
	if ((state & BIT(4)) || !(state & GENMASK(1, 0)) ||
	    (state & 0xc0) != 0x40 || max_charge != OBSERVED_MAX_CHARGE ||
	    (*limit != HANDOFF_LIMIT && *limit != SAFE_USB_LIMIT)) {
		pr_warn("j71b charge lease: PMIC preflight failed (state=0x%x max=0x%x limit=0x%x)\n",
			state, max_charge, *limit);
		return -EPERM;
	}
	return 0;
}

static int set_lease(const char *val, const struct kernel_param *kp)
{
	unsigned int input, readback;
	bool on;
	int ret;

	ret = kstrtobool(val, &on);
	if (ret) return ret;
	if (!READ_ONCE(ready)) return -ENODEV;
	if (!on) {
		cancel_delayed_work_sync(&lease_work);
		mutex_lock(&policy_lock);
		ret = restore_locked();
		if (!ret) {
			ret = regmap_read(map, INPUT_LIMIT_REG, &readback);
			if (!ret && readback != HANDOFF_LIMIT) ret = -EPERM;
		}
		restore_failed = !!ret;
		mutex_unlock(&policy_lock);
		return ret;
	}
	mutex_lock(&policy_lock);
	if (parked || restore_failed) {
		mutex_unlock(&policy_lock);
		return -EBUSY;
	}
	ret = check_battery();
	if (ret) goto stop;
	ret = baseline(&input);
	if (ret) goto stop;
	if (input == SAFE_USB_LIMIT && !changed) {
		ret = -EPERM; /* Never adopt an unexplained preexisting limit. */
		goto stop;
	}
	if (input == HANDOFF_LIMIT) {
		changed = true; /* Retain recovery intent even if readback fails. */
		mod_delayed_work(system_unbound_wq, &lease_work,
				 LEASE_SECONDS * HZ);
		ret = regmap_write(map, INPUT_LIMIT_REG, SAFE_USB_LIMIT);
		if (ret) goto stop;
		ret = regmap_read(map, INPUT_LIMIT_REG, &readback);
		if (ret || readback != SAFE_USB_LIMIT) {
			ret = ret ?: -EIO;
			goto stop;
		}
		pr_info("j71b charge lease: input limit 0x1c verified (425mA cap)\n");
	}
	mod_delayed_work(system_unbound_wq, &lease_work, LEASE_SECONDS * HZ);
	mutex_unlock(&policy_lock);
	return 0;
stop:
	if (changed)
		restore_locked();
	mutex_unlock(&policy_lock);
	return ret;
}

/* Called before userspace freezing/device suspend, and after device resume.
 * Mark parked before cancellation, under the same lock used by set_lease:
 * a racing renewer either finishes first and is cancelled/restored, or refuses.
 * Never hold policy_lock across cancel_delayed_work_sync().
 */
static int policy_pm(struct notifier_block *nb, unsigned long event, void *unused)
{
	unsigned int input;
	int ret;

	switch (event) {
	case PM_SUSPEND_PREPARE:
	case PM_HIBERNATION_PREPARE:
	case PM_RESTORE_PREPARE:
		mutex_lock(&policy_lock);
		parked = true;
		mutex_unlock(&policy_lock);
		cancel_delayed_work_sync(&lease_work);
		mutex_lock(&policy_lock);
		ret = restore_locked();
		if (!ret) {
			ret = regmap_read(map, INPUT_LIMIT_REG, &input);
			if (!ret && input != HANDOFF_LIMIT)
				ret = -EPERM;
		}
		restore_failed = !!ret;
		if (ret) {
			/* The failing notifier itself does not receive robust rollback. */
			parked = false;
			pr_err("j71b charge: suspend refused: handoff restore/readback failed (%d)\n", ret);
		} else {
			pr_info("j71b charge: sleep parked, watchdog cancelled, handoff verified\n");
		}
		mutex_unlock(&policy_lock);
		return notifier_from_errno(ret);
	case PM_POST_SUSPEND:
	case PM_POST_HIBERNATION:
	case PM_POST_RESTORE:
		mutex_lock(&policy_lock);
		parked = false;
		mutex_unlock(&policy_lock);
		/* No write or lease replay here. A fresh userspace renewal must
		 * recheck source presence, handoff and all pack guards. */
		pr_info("j71b charge: resumed; fresh guarded renewal required\n");
		return NOTIFY_OK;
	default:
		return NOTIFY_DONE;
	}
}
static struct notifier_block policy_pm_nb = { .notifier_call = policy_pm };

static const struct kernel_param_ops lease_ops = { .set = set_lease };
module_param_cb(lease, &lease_ops, NULL, 0200);
MODULE_PARM_DESC(lease, "Root-only 15s charger lease: 1 renews, 0 restores");

static int __init policy_init(void)
{
	struct power_supply *other;
	unsigned int input, max_charge;
	int ret;

	if (!of_machine_is_compatible("apple,j71b")) return -ENODEV;
	/* Loading does not request charge: allow high/low SOC or absent input.
	 * Every actual renewal still requires the unchanged pack/source guards. */
	pmic = bus_find_device_by_name(&i2c_bus_type, NULL, "0-003c");
	if (!pmic) return -ENODEV;
	if (!pmic->of_node || !of_device_is_compatible(pmic->of_node,
						      "apple,angel-pmic")) {
		ret = -ENODEV;
		goto out;
	}
	map = dev_get_regmap(pmic, NULL);
	if (!map) { ret = -ENODEV; goto out; }
	other = power_supply_get_by_name("j71b-charger");
	if (other) {
		/* Do not race another experimental PMIC writer. */
		power_supply_put(other);
		ret = -EBUSY;
		goto out;
	}
	ret = regmap_read(map, MAX_CHARGE_REG, &max_charge);
	if (ret) goto out;
	ret = regmap_read(map, INPUT_LIMIT_REG, &input);
	if (ret) goto out;
	if (input != HANDOFF_LIMIT || max_charge != OBSERVED_MAX_CHARGE) {
		ret = -EPERM; goto out;
	}
	ret = register_pm_notifier(&policy_pm_nb);
	if (ret) goto out;
	WRITE_ONCE(ready, true);
	input_supply = power_supply_register(pmic, &input_desc, NULL);
	if (IS_ERR(input_supply)) {
		ret = PTR_ERR(input_supply);
		input_supply = NULL;
		WRITE_ONCE(ready, false);
		unregister_pm_notifier(&policy_pm_nb);
		goto out;
	}
	pr_info("j71b charge lease: ready, handoff input 0x02; no register write at load\n");
	return 0;
out:
	put_device(pmic);
	pmic = NULL;
	map = NULL;
	return ret;
}

static void __exit policy_exit(void)
{
	WRITE_ONCE(ready, false);
	power_supply_unregister(input_supply);
	unregister_pm_notifier(&policy_pm_nb);
	cancel_delayed_work_sync(&lease_work);
	mutex_lock(&policy_lock);
	restore_locked();
	mutex_unlock(&policy_lock);
	put_device(pmic);
}
module_init(policy_init);
module_exit(policy_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J71b <=425mA guarded lease with PM-safe handoff and read-only input presence");

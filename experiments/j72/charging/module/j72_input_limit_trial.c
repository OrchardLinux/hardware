// SPDX-License-Identifier: GPL-2.0-only
/* One manually armed, physical-Home-triggered 425mA/30s J72 experiment.
 * Only PMIC register 04c0 is writable. No write on load or arm.
 * The high-priority deadline never reads the gauge. Gauge reads occur
 * outside policy_lock, on a separate queue, so HDQ waits cannot block it.
 */
#include <linux/device.h>
#include <linux/i2c.h>
#include <linux/init.h>
#include <linux/input.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/power/bq27xxx_battery.h>
#include <linux/power_supply.h>
#include <linux/reboot.h>
#include <linux/regmap.h>
#include <linux/slab.h>
#include <linux/suspend.h>
#include <linux/utsname.h>
#include <linux/workqueue.h>
#include "j72-limit-core.h"

#define EXPECTED_BUILD "#33 SMP PREEMPT Sat Oct 31 11:57:21 +08 2026"
#define PMIC_PATH "/soc/i2c@20a110000/pmic@3c"
#define BATTERY_PATH "/soc/serial@20a0b4000/battery"

static DEFINE_MUTEX(policy_lock);
static struct j72_limit_state policy;
static struct device *pmic;
static struct regmap *map;
static struct power_supply *battery;
static struct workqueue_struct *deadline_queue, *guard_queue;
static bool ready, armed, power_down, home_pressed;
static struct input_handle *home_handle;
static unsigned int samples;
static int baseline_current, current_min, current_max;

static void deadline_fn(struct work_struct *work);
static void guard_fn(struct work_struct *work);
static void request_fn(struct work_struct *work);
static void abort_fn(struct work_struct *work);
static DECLARE_DELAYED_WORK(deadline_work, deadline_fn);
static DECLARE_DELAYED_WORK(guard_work, guard_fn);
static DECLARE_WORK(request_work, request_fn);
static DECLARE_WORK(abort_work, abort_fn);

static u64 now_ms(void)
{
	return ktime_to_ms(ktime_get_boottime());
}

static int read_limit(void *unused, unsigned int *value)
{
	return regmap_read_bypassed(map, 0x04c0, value);
}

static int write_limit(void *unused, unsigned int value)
{
	u64 now, remaining;

	if (value != J72_HANDOFF && value != J72_TRIAL)
		return -EPERM;
	if (value == J72_TRIAL) {
		/* This callback is reached only after the core records ownership. */
		now = now_ms();
		remaining = policy.deadline_ms > now ? policy.deadline_ms - now : 0;
		if (!remaining)
			return -ETIME;
		mod_delayed_work(deadline_queue, &deadline_work,
				 msecs_to_jiffies(remaining));
	}
	return regmap_write(map, 0x04c0, value);
}

static const struct j72_limit_io io = {
	.read = read_limit,
	.write = write_limit,
};

static int pmic_guard(void)
{
	static const unsigned int ceiling_regs[] = {0x04c6, 0x04c7, 0x04ce};
	unsigned int value, i;
	int ret;

	ret = regmap_read_bypassed(map, 0x0010, &value);
	if (ret || (value & BIT(2)))
		return ret ? ret : -EPERM;
	for (i = 0; i < ARRAY_SIZE(ceiling_regs); i++) {
		ret = regmap_read_bypassed(map, ceiling_regs[i], &value);
		if (ret || value != 0x3f)
			return ret ? ret : -EPERM;
	}
	return 0;
}

/* Never hold policy_lock around this function: a gauge access can wait. */
static int pack_guard(int *pack_current)
{
	static const enum power_supply_property props[] = {
		POWER_SUPPLY_PROP_PRESENT, POWER_SUPPLY_PROP_CAPACITY,
		POWER_SUPPLY_PROP_TEMP, POWER_SUPPLY_PROP_VOLTAGE_NOW,
		POWER_SUPPLY_PROP_CURRENT_NOW,
	};
	union power_supply_propval v;
	int values[ARRAY_SIZE(props)], ret;
	unsigned int i;

	for (i = 0; i < ARRAY_SIZE(props); i++) {
		ret = power_supply_get_property(battery, props[i], &v);
		if (ret)
			return ret;
		values[i] = v.intval;
	}
	*pack_current = values[4];
	return j72_limit_pack_guard(values[0], values[1], values[2],
				    values[3], values[4]);
}

static void finish_locked(void)
{
	WRITE_ONCE(armed, false);
	pr_info("j72 input trial: active=%u restored=%u restore_failed=%u error=%d samples=%u\n",
		policy.active, policy.restored, policy.restore_failed,
		policy.error, samples);
}

static void deadline_fn(struct work_struct *work)
{
	u64 now;

	mutex_lock(&policy_lock);
	if (policy.active) {
		now = now_ms();
		j72_limit_check(&policy, &io, now, 0);
		if (policy.active)
			mod_delayed_work(deadline_queue, &deadline_work,
				msecs_to_jiffies(policy.deadline_ms - now));
		else
			finish_locked();
	}
	mutex_unlock(&policy_lock);
}

static void guard_fn(struct work_struct *work)
{
	int ret, pack_current = 0;

	if (!READ_ONCE(ready))
		return;
	mutex_lock(&policy_lock);
	if (!policy.active) {
		mutex_unlock(&policy_lock);
		return;
	}
	mutex_unlock(&policy_lock);
	ret = pack_guard(&pack_current);
	mutex_lock(&policy_lock);
	if (policy.active) {
		if (!ret)
			ret = pmic_guard();
		if (!ret && now_ms() >= policy.deadline_ms - 25000 &&
		    now_ms() < policy.deadline_ms) {
			if (!samples || pack_current < current_min) current_min = pack_current;
			if (!samples || pack_current > current_max) current_max = pack_current;
			samples++;
		}
		j72_limit_check(&policy, &io, now_ms(), ret);
		if (policy.active)
			queue_delayed_work(guard_queue, &guard_work, 2 * HZ);
		else
			finish_locked();
	}
	mutex_unlock(&policy_lock);
}

static void request_fn(struct work_struct *work)
{
	int ret, pack_current = 0;

	if (!READ_ONCE(ready) || !READ_ONCE(armed) || READ_ONCE(power_down))
		return;
	ret = pack_guard(&pack_current);
	mutex_lock(&policy_lock);
	if (!ready || !armed || power_down || policy.used || policy.active) {
		mutex_unlock(&policy_lock);
		return;
	}
	if (!ret) ret = pmic_guard();
	baseline_current = pack_current;
	ret = j72_limit_start(&policy, &io, now_ms(), ret);
	if (!ret) {
		pr_info("j72 input trial: physical Home, 425mA setting verified, 30s maximum\n");
		queue_delayed_work(guard_queue, &guard_work, HZ);
	} else {
		finish_locked();
	}
	mutex_unlock(&policy_lock);
}

static void abort_fn(struct work_struct *work)
{
	mutex_lock(&policy_lock);
	WRITE_ONCE(armed, false);
	j72_limit_restore(&policy, &io);
	mutex_unlock(&policy_lock);
}

static void home_event(struct input_handle *handle, unsigned int type,
		       unsigned int code, int value)
{
	if (type != EV_KEY || !READ_ONCE(ready)) return;
	if (code == KEY_POWER) {
		WRITE_ONCE(power_down, value != 0);
		if (value == 1) {
			WRITE_ONCE(armed, false);
			queue_work(deadline_queue, &abort_work);
		}
		return;
	}
	if (code != KEY_HOMEPAGE) return;
	if (value == 1) home_pressed = true;
	if (value == 0 && home_pressed) {
		home_pressed = false;
		if (READ_ONCE(armed) && !READ_ONCE(power_down))
			queue_work(system_unbound_wq, &request_work);
	}
}

static int home_connect(struct input_handler *handler, struct input_dev *dev,
			const struct input_device_id *id)
{
	struct input_handle *handle;
	int ret;

	if (!dev->name || strcmp(dev->name, "gpio-keys") ||
	    !test_bit(KEY_HOMEPAGE, dev->keybit)) return -ENODEV;
	if (home_handle) return -EBUSY;
	handle = kzalloc(sizeof(*handle), GFP_KERNEL);
	if (!handle) return -ENOMEM;
	handle->dev = dev; handle->handler = handler; handle->name = "j72-input-trial";
	ret = input_register_handle(handle);
	if (ret) goto free;
	ret = input_open_device(handle);
	if (ret) { input_unregister_handle(handle); goto free; }
	home_handle = handle;
	return 0;
free:
	kfree(handle);
	return ret;
}

static void home_disconnect(struct input_handle *handle)
{
	WRITE_ONCE(armed, false);
	input_close_device(handle);
	input_unregister_handle(handle);
	home_handle = NULL;
	if (READ_ONCE(ready)) queue_work(deadline_queue, &abort_work);
	kfree(handle);
}

static const struct input_device_id home_ids[] = {
	{ .flags = INPUT_DEVICE_ID_MATCH_EVBIT, .evbit = { BIT_MASK(EV_KEY) } },
	{ },
};
static struct input_handler home_handler = {
	.event = home_event, .connect = home_connect, .disconnect = home_disconnect,
	.name = "j72-input-trial", .id_table = home_ids,
};

static int arm_set(const char *value, const struct kernel_param *unused)
{
	int ret = 0, pack_current = 0;
	unsigned int limit;
	bool on;

	if (sysfs_streq(value, "425mA-on-wall")) on = true;
	else if (sysfs_streq(value, "stop")) on = false;
	else return -EINVAL;
	if (!READ_ONCE(ready)) return -ENODEV;
	if (on) ret = pack_guard(&pack_current); /* No policy lock during HDQ. */
	mutex_lock(&policy_lock);
	WRITE_ONCE(armed, false);
	if (!on) {
		ret = j72_limit_restore(&policy, &io);
	} else if (!ready || !home_handle || policy.used || policy.restore_failed || power_down) {
		ret = -EPERM;
	} else if (!ret) {
		ret = pmic_guard();
		if (!ret) ret = read_limit(NULL, &limit);
		if (!ret && limit != J72_HANDOFF) ret = -EPERM;
		if (!ret) WRITE_ONCE(armed, true);
	}
	mutex_unlock(&policy_lock);
	return ret;
}
static const struct kernel_param_ops arm_ops = { .set = arm_set };
module_param_cb(arm_wall, &arm_ops, NULL, 0200);

static int status_get(char *buf, const struct kernel_param *unused)
{
	u64 remaining, now;
	int n;

	mutex_lock(&policy_lock);
	now = now_ms();
	remaining = policy.active && policy.deadline_ms > now ? policy.deadline_ms - now : 0;
	n = scnprintf(buf, PAGE_SIZE,
		"ready=%u\narmed=%u\nused=%u\nactive=%u\nrestored=%u\nrestore_failed=%u\nerror=%d\nremaining_ms=%llu\nbaseline_current_uA=%d\ntrial_samples=%u\ntrial_current_min_uA=%d\ntrial_current_max_uA=%d\n",
		ready, armed, policy.used, policy.active, policy.restored,
		policy.restore_failed, policy.error, (unsigned long long)remaining,
		baseline_current, samples, current_min, current_max);
	mutex_unlock(&policy_lock);
	return n;
}
static const struct kernel_param_ops status_ops = { .get = status_get };
module_param_cb(status, &status_ops, NULL, 0400);

static int prepare_stop(void)
{
	int ret;
	WRITE_ONCE(armed, false);
	mutex_lock(&policy_lock);
	ret = j72_limit_restore(&policy, &io);
	mutex_unlock(&policy_lock);
	return ret;
}
static int pm_event(struct notifier_block *nb, unsigned long event, void *unused)
{
	if (event == PM_SUSPEND_PREPARE || event == PM_HIBERNATION_PREPARE ||
	    event == PM_RESTORE_PREPARE) return notifier_from_errno(prepare_stop());
	return NOTIFY_DONE;
}
static int reboot_event(struct notifier_block *nb, unsigned long event, void *unused)
{
	prepare_stop();
	return NOTIFY_DONE;
}
static struct notifier_block pm_nb = { .notifier_call = pm_event };
static struct notifier_block reboot_nb = { .notifier_call = reboot_event };

static int exact_pmic(struct device *dev, const void *node)
{
	struct i2c_client *c = i2c_verify_client(dev);
	return c && c->addr == 0x3c && dev->of_node == node &&
		of_device_is_compatible(dev->of_node, "apple,anya-pmic");
}

static int __init trial_init(void)
{
	struct device_node *node;
	struct bq27xxx_device_info *di;
	unsigned int limit;
	int ret = -ENODEV;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(utsname()->version, EXPECTED_BUILD)) return -ENODEV;
	node = of_find_node_by_path(PMIC_PATH);
	if (!node) return -ENODEV;
	pmic = bus_find_device(&i2c_bus_type, NULL, node, exact_pmic);
	of_node_put(node);
	if (!pmic) return -ENODEV;
	map = dev_get_regmap(pmic, NULL);
	if (!pmic->driver || !map || regmap_get_val_bytes(map) != 1) goto put_pmic;
	battery = power_supply_get_by_name("j72-bq27545-battery");
	if (!battery) goto put_pmic;
	di = power_supply_get_drvdata(battery);
	node = of_find_node_by_path(BATTERY_PATH);
	if (!di || !node || di->dev->of_node != node || di->chip != BQ27545 ||
	    strcmp(dev_driver_string(di->dev), "bq27xxx-hdq-uart")) {
		of_node_put(node); goto put_battery;
	}
	of_node_put(node);
	ret = pmic_guard();
	if (!ret) ret = read_limit(NULL, &limit);
	if (!ret && limit != J72_HANDOFF) ret = -EPERM;
	if (ret) goto put_battery;
	deadline_queue = alloc_workqueue("j72-limit-deadline", WQ_UNBOUND | WQ_HIGHPRI | WQ_MEM_RECLAIM, 1);
	guard_queue = alloc_workqueue("j72-limit-gauge", WQ_UNBOUND | WQ_MEM_RECLAIM, 1);
	if (!deadline_queue || !guard_queue) { ret = -ENOMEM; goto destroy; }
	ret = register_pm_notifier(&pm_nb);
	if (ret) goto destroy;
	ret = register_reboot_notifier(&reboot_nb);
	if (ret) goto unreg_pm;
	ret = input_register_handler(&home_handler);
	if (ret) goto unreg_reboot;
	if (!home_handle) { ret = -ENODEV; goto unreg_input; }
	WRITE_ONCE(ready, true);
	pr_info("j72 input trial: ready, unarmed, 425mA/30s/one physical request; NO write at load\n");
	return 0;
unreg_input:
	input_unregister_handler(&home_handler);
unreg_reboot:
	unregister_reboot_notifier(&reboot_nb);
unreg_pm:
	unregister_pm_notifier(&pm_nb);
destroy:
	if (deadline_queue) destroy_workqueue(deadline_queue);
	if (guard_queue) destroy_workqueue(guard_queue);
put_battery:
	power_supply_put(battery);
put_pmic:
	put_device(pmic);
	return ret;
}

static void __exit trial_exit(void)
{
	WRITE_ONCE(ready, false);
	prepare_stop();
	input_unregister_handler(&home_handler);
	unregister_reboot_notifier(&reboot_nb);
	unregister_pm_notifier(&pm_nb);
	cancel_work_sync(&request_work);
	cancel_work_sync(&abort_work);
	cancel_delayed_work_sync(&guard_work);
	cancel_delayed_work_sync(&deadline_work);
	destroy_workqueue(guard_queue);
	destroy_workqueue(deadline_queue);
	power_supply_put(battery);
	put_device(pmic);
}
module_init(trial_init);
module_exit(trial_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Exact J72/#33 single 425mA input-limit comparison with 30s rollback");

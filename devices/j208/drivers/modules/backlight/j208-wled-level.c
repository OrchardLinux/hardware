// SPDX-License-Identifier: GPL-2.0-only
/* J208 RAM brightness: only the native high-then-low WLED DAC pair.
 * Gate operations remain with j208-wled-gate. Userspace shares its button lock.
 * No gate, panel, charge, voltage, DMA or storage writes.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regmap.h>

struct level_state {
	struct regmap *map;
	unsigned int boot, minimum, level, gates[3];
	int fault, restore;
	bool dirty;
};

static struct level_state state;
static struct i2c_client *client;
static DEFINE_MUTEX(level_lock);
static bool ready;
module_param_named(boot_level, state.boot, uint, 0400);
module_param_named(min_level, state.minimum, uint, 0400);
module_param_named(fault_rc, state.fault, int, 0400);
module_param_named(restore_rc, state.restore, int, 0400);

static int read_state(struct regmap *map, unsigned int *values)
{
	static const unsigned int regs[] = { 0x307, 0x308, 0x309, 0xb01, 0xb00 };
	int i, ret;

	for (i = 0; i < ARRAY_SIZE(regs); i++) {
		/* Read hardware, not a cached software copy of the last write. */
		ret = regmap_read_bypassed(map, regs[i], &values[i]);
		if (ret)
			return ret;
	}
	return values[3] > 7 || values[4] > 255 ? -ERANGE : 0;
}

static int restore_level(struct level_state *s)
{
	unsigned int values[5];
	int first, second, check;

	/* Attempt both halves, even when a transaction reports failure. */
	first = regmap_write(s->map, 0xb01, s->boot >> 8);
	second = regmap_write(s->map, 0xb00, s->boot & 255);
	check = read_state(s->map, values);
	if (!check && (values[3] << 8 | values[4]) != s->boot)
		check = -EIO;
	s->restore = first ? first : second ? second : check;
	if (!s->restore) {
		s->level = s->boot;
		s->dirty = false;
	}
	return s->restore;
}

static int change_level(struct level_state *s, unsigned int target)
{
	unsigned int before[5], after[5];
	int ret;

	if (s->fault)
		return s->fault;
	if (target < s->minimum || target > s->boot)
		return -EINVAL;
	ret = read_state(s->map, before);
	if (ret)
		goto fault;
	/* A powered-off screen must be woken through the existing gate path. */
	if (memcmp(before, s->gates, sizeof(s->gates)))
		return -EBUSY;
	if ((before[3] << 8 | before[4]) != s->level) {
		ret = -ESTALE;
		goto fault;
	}
	if (target == s->level)
		return 0;
	s->dirty = true;
	ret = regmap_write(s->map, 0xb01, target >> 8);
	if (!ret)
		ret = regmap_write(s->map, 0xb00, target & 255);
	if (!ret)
		ret = read_state(s->map, after);
	if (!ret && (memcmp(before, after, sizeof(s->gates)) ||
		     (after[3] << 8 | after[4]) != target))
		ret = -EIO;
	if (ret) {
		restore_level(s);
		goto fault;
	}
	s->level = target;
	s->dirty = target != s->boot;
	return 0;
fault:
	s->fault = ret;
	return ret;
}

static int set_level(const char *value, const struct kernel_param *parameter)
{
	unsigned int target;
	int ret = kstrtouint(value, 0, &target);

	if (ret)
		return ret;
	mutex_lock(&level_lock);
	ret = ready ? change_level(&state, target) : -EAGAIN;
	mutex_unlock(&level_lock);
	return ret;
}

static int get_level(char *buffer, const struct kernel_param *parameter)
{
	int ret;

	mutex_lock(&level_lock);
	ret = ready ? scnprintf(buffer, PAGE_SIZE, "%u\n", state.level) : -EAGAIN;
	mutex_unlock(&level_lock);
	return ret;
}

static const struct kernel_param_ops level_ops = {
	.set = set_level,
	.get = get_level,
};
module_param_cb(level, &level_ops, NULL, 0600);

static int __init j208_level_init(void)
{
	struct device_node *node;
	unsigned int values[5], again[5];
	int ret = -ENODEV;

	if (!of_machine_is_compatible("apple,j208"))
		return ret;
	node = of_find_node_by_path("/soc/i2c@20a110000/pmic@3c");
	if (!node)
		return ret;
	if (of_device_is_compatible(node, "apple,august-pmic"))
		client = of_find_i2c_device_by_node(node);
	of_node_put(node);
	if (!client)
		return ret;
	state.map = dev_get_regmap(&client->dev, NULL);
	if (!state.map)
		goto release;
	ret = read_state(state.map, values);
	if (!ret)
		ret = read_state(state.map, again);
	if (ret)
		goto release;
	if (memcmp(values, again, sizeof(values)) || values[0] != 1 ||
	    values[1] > 1 || values[2] != 0) {
		ret = -EINVAL;
		goto release;
	}
	state.boot = values[3] << 8 | values[4];
	if (state.boot < 4) {
		ret = -ERANGE;
		goto release;
	}
	state.minimum = (state.boot + 3) / 4;
	state.level = state.boot;
	memcpy(state.gates, values, sizeof(state.gates));
	ready = true;
	pr_info("J208_WLED_LEVEL ready boot=%u minimum=%u; probe_writes=0\n",
		state.boot, state.minimum);
	return 0;
release:
	put_device(&client->dev);
	return ret;
}

static void __exit j208_level_exit(void)
{
	mutex_lock(&level_lock);
	ready = false;
	if (state.dirty && restore_level(&state))
		pr_err("J208_WLED_LEVEL unload restore failed: %d\n", state.restore);
	mutex_unlock(&level_lock);
	put_device(&client->dev);
}

module_init(j208_level_init);
module_exit(j208_level_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 RAM WLED DAC control bounded by captured boot brightness");

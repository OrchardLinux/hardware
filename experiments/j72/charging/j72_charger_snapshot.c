// SPDX-License-Identifier: GPL-2.0-only
/* Exact J72/#33 read-only D2089 observer. No workqueue, timer or writes.
 * Reading the root-only snapshot parameter reads a fixed allowlist through
 * the existing PMIC regmap, bypassing cache. No event-ack/ADC/timer access.
 */
#include <linux/device.h>
#include <linux/i2c.h>
#include <linux/init.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/regmap.h>
#include <linux/utsname.h>

#define EXPECTED_BUILD "#33 SMP PREEMPT Sat Oct 31 11:57:21 +08 2026"
#define PMIC_PATH "/soc/i2c@20a110000/pmic@3c"

static const unsigned int registers[] = {
	0x0010, 0x0060, 0x0061, 0x0062, 0x0063, 0x0064, 0x0065,
	0x0066, 0x0067, 0x0068, 0x0069, 0x006a, 0x006b, 0x006c,
	0x04c0, 0x04c6, 0x04c7, 0x04ce,
};
static struct device *pmic;
static struct regmap *map;
static DEFINE_MUTEX(capture_lock);

static int exact_pmic(struct device *dev, const void *expected)
{
	struct i2c_client *client = i2c_verify_client(dev);

	return client && client->addr == 0x3c && dev->of_node == expected &&
		of_device_is_compatible(dev->of_node, "apple,anya-pmic");
}

static int get_snapshot(char *buf, const struct kernel_param *unused)
{
	unsigned int i, value;
	int ret, length;

	mutex_lock(&capture_lock);
	if (!map || !pmic || !pmic->driver) {
		ret = -ENODEV;
		goto out;
	}
	length = scnprintf(buf, PAGE_SIZE, "uptime_ms=%llu\n",
			   (unsigned long long)ktime_to_ms(ktime_get_boottime()));
	for (i = 0; i < ARRAY_SIZE(registers); i++) {
		ret = regmap_read_bypassed(map, registers[i], &value);
		if (ret)
			goto out;
		length += scnprintf(buf + length, PAGE_SIZE - length,
				    "reg_%04x=%02x\n", registers[i], value & 0xff);
	}
	ret = length;
out:
	mutex_unlock(&capture_lock);
	return ret;
}

static const struct kernel_param_ops snapshot_ops = {
	.get = get_snapshot,
};
module_param_cb(snapshot, &snapshot_ops, NULL, 0400);
MODULE_PARM_DESC(snapshot, "Read fixed D2089 power-source registers; no writes");

static int __init snapshot_init(void)
{
	struct device_node *node;

	if (!of_machine_is_compatible("apple,j72") ||
	    strcmp(utsname()->version, EXPECTED_BUILD))
		return -ENODEV;
	node = of_find_node_by_path(PMIC_PATH);
	if (!node)
		return -ENODEV;
	pmic = bus_find_device(&i2c_bus_type, NULL, node, exact_pmic);
	of_node_put(node);
	if (!pmic)
		return -ENODEV;
	map = dev_get_regmap(pmic, NULL);
	if (!pmic->driver || !map || regmap_get_val_bytes(map) != 1) {
		put_device(pmic);
		pmic = NULL;
		map = NULL;
		return -ENODEV;
	}
	pr_info("j72_charger_snapshot: exact J72/#33 read-only observer ready\n");
	return 0;
}

static void __exit snapshot_exit(void)
{
	mutex_lock(&capture_lock);
	map = NULL;
	put_device(pmic);
	pmic = NULL;
	mutex_unlock(&capture_lock);
}

module_init(snapshot_init);
module_exit(snapshot_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Exact J72/#33 fixed D2089 power-source register observer");

// SPDX-License-Identifier: GPL-2.0-only
/* J208 RAM bring-up: only D2400 LDO13 enable and D2401 WLAN GPIO1.
 * Register semantics are traced in pmic-clock-evidence.md. No voltage writes.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/regulator/driver.h>
#include <linux/regulator/of_regulator.h>

struct j208_power_info {
	const char *parent_compatible;
	unsigned int reg;
	unsigned int mask;
	bool require_output_mode;
};

struct j208_power {
	struct regmap *map;
	const struct j208_power_info *info;
	struct regulator_desc desc;
};

static int j208_power_check(struct j208_power *power, unsigned int *value)
{
	int ret = regmap_read(power->map, power->info->reg, value);

	if (ret)
		return ret;
	/* Native D2401 code treats values below 0x40 as input mode. Do not
	 * invent a mode transition when the boot firmware has not configured it.
	 */
	if (power->info->require_output_mode && *value < 0x40)
		return -EOPNOTSUPP;
	return 0;
}

static int j208_power_set(struct regulator_dev *rdev, bool enable)
{
	struct j208_power *power = rdev_get_drvdata(rdev);
	unsigned int value;
	int ret = j208_power_check(power, &value);

	if (ret)
		return ret;
	return regmap_update_bits(power->map, power->info->reg,
				  power->info->mask, enable ? 1 : 0);
}

static int j208_power_enable(struct regulator_dev *rdev)
{
	return j208_power_set(rdev, true);
}

static int j208_power_disable(struct regulator_dev *rdev)
{
	return j208_power_set(rdev, false);
}

static int j208_power_is_enabled(struct regulator_dev *rdev)
{
	struct j208_power *power = rdev_get_drvdata(rdev);
	unsigned int value;
	int ret = j208_power_check(power, &value);

	return ret ? ret : !!(value & 1);
}

static const struct regulator_ops j208_power_ops = {
	.enable = j208_power_enable,
	.disable = j208_power_disable,
	.is_enabled = j208_power_is_enabled,
};

static int j208_power_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct regulator_config config = {};
	struct regulator_dev *rdev;
	struct j208_power *power;
	unsigned int value;
	int ret;

	if (!of_machine_is_compatible("apple,j208"))
		return -ENODEV;
	power = devm_kzalloc(dev, sizeof(*power), GFP_KERNEL);
	if (!power)
		return -ENOMEM;
	power->info = device_get_match_data(dev);
	if (!power->info || !dev->parent ||
	    !of_device_is_compatible(dev->parent->of_node,
				     power->info->parent_compatible))
		return -ENODEV;
	power->map = dev_get_regmap(dev->parent, NULL);
	if (!power->map)
		return -EPROBE_DEFER;
	ret = j208_power_check(power, &value);
	if (ret)
		return dev_err_probe(dev, ret, "unverified PMIC output state\n");
	power->desc.name = dev_name(dev);
	power->desc.type = REGULATOR_VOLTAGE;
	power->desc.owner = THIS_MODULE;
	power->desc.ops = &j208_power_ops;
	config.dev = dev;
	config.of_node = dev->of_node;
	config.driver_data = power;
	config.init_data = of_get_regulator_init_data(dev, dev->of_node, &power->desc);
	rdev = devm_regulator_register(dev, &power->desc, &config);
	if (IS_ERR(rdev))
		return PTR_ERR(rdev);
	dev_info(dev, "J208 enable-only supply, register %#x initial %#x\n",
		 power->info->reg, value);
	return 0;
}

static const struct j208_power_info touch_analog = {
	.parent_compatible = "apple,august-pmic",
	.reg = 0x312,
	.mask = 1,
};

static const struct j208_power_info wlan = {
	.parent_compatible = "apple,autumn-pmic",
	.reg = 0x902,
	.mask = 0x19,
	.require_output_mode = true,
};

static const struct of_device_id j208_power_matches[] = {
	{ .compatible = "orchard,j208-touch-analog", .data = &touch_analog },
	{ .compatible = "orchard,j208-wlan-enable", .data = &wlan },
	{}
};
MODULE_DEVICE_TABLE(of, j208_power_matches);

static struct platform_driver j208_power_driver = {
	.probe = j208_power_probe,
	.driver = {
		.name = "j208-pmic-power",
		.of_match_table = j208_power_matches,
	},
};
module_platform_driver(j208_power_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 RAM test enable-only touch and WLAN supplies");

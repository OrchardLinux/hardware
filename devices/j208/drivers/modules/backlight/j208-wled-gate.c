// SPDX-License-Identifier: GPL-2.0-only
/* J208 RAM handoff test: restore the captured gates, preserve DAC/current.
 * Native 21H461 AppleD2400PMU bklE: 0xfffffff0064be1d8..64be384.
 * This is neither panel suspend nor a system sleep implementation.
 */
#include <linux/backlight.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>

struct j208_gate {
	struct device *dev;
	struct regmap *map;
	struct mutex lock;
	unsigned int saved[3];
	bool dirty;
};

static int j208_gate_capture(struct j208_gate *gate)
{
	int i, ret;

	for (i = 0; i < 3; i++) {
		ret = regmap_read(gate->map, 0x307 + i, &gate->saved[i]);
		if (ret)
			return ret;
	}
	dev_info(gate->dev,
		 "WLED handoff gates 307=%02x 308=%02x 309=%02x; read-only check\n",
		 gate->saved[0], gate->saved[1], gate->saved[2]);
	/* Accept only observed 1/0/0 or native full-on 1/1/0, as whole bytes. */
	if (gate->saved[0] != 1 || gate->saved[1] > 1 || gate->saved[2] != 0)
		return -EOPNOTSUPP;
	return 0;
}

static int j208_gate_apply(struct j208_gate *gate, bool on)
{
	int i, ret;

	/* Mark before the first write: even a failed write can have taken effect. */
	gate->dirty = true;
	for (i = 0; i < 3; i++) {
		ret = regmap_write(gate->map, 0x307 + i,
				   on ? gate->saved[i] : 0);
		if (ret)
			return ret;
	}
	return 0;
}

static int j208_gate_update(struct backlight_device *bl)
{
	struct j208_gate *gate = bl_get_data(bl);
	int ret;

	mutex_lock(&gate->lock);
	ret = j208_gate_apply(gate, backlight_get_brightness(bl) != 0);
	mutex_unlock(&gate->lock);
	return ret;
}

static void j208_gate_restore(void *arg)
{
	struct j208_gate *gate = arg;
	int i, ret;

	if (!gate->dirty)
		return;
	/* Attempt every saved gate, even if one I2C transaction fails. */
	for (i = 0; i < 3; i++) {
		ret = regmap_write(gate->map, 0x307 + i, gate->saved[i]);
		if (ret)
			dev_err(gate->dev, "WLED restore gate %d failed: %d\n", i, ret);
	}
}

static const struct backlight_ops j208_gate_ops = {
	.update_status = j208_gate_update,
};

static int j208_gate_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct backlight_properties props = {
		.type = BACKLIGHT_RAW,
		.max_brightness = 1,
		.brightness = 1,
		.power = BACKLIGHT_POWER_ON,
		.scale = BACKLIGHT_SCALE_UNKNOWN,
	};
	struct backlight_device *bl;
	struct j208_gate *gate;
	int ret;

	if (!of_machine_is_compatible("apple,j208") || !dev->parent ||
	    !of_device_is_compatible(dev->parent->of_node, "apple,august-pmic"))
		return -ENODEV;
	gate = devm_kzalloc(dev, sizeof(*gate), GFP_KERNEL);
	if (!gate)
		return -ENOMEM;
	gate->dev = dev;
	gate->map = dev_get_regmap(dev->parent, NULL);
	if (!gate->map)
		return -EPROBE_DEFER;
	mutex_init(&gate->lock);
	ret = j208_gate_capture(gate);
	if (ret)
		return dev_err_probe(dev, ret,
				     "Unqualified WLED handoff; no writes performed\n");
	ret = devm_add_action_or_reset(dev, j208_gate_restore, gate);
	if (ret)
		return ret;
	/* Registered after restore action: backlight unregisters before restore. */
	bl = devm_backlight_device_register(dev, "j208-wled-gate", dev, gate,
					   &j208_gate_ops, &props);
	if (IS_ERR(bl))
		return PTR_ERR(bl);
	dev_info(dev, "handoff-state-v1 ready; restoring saved gates only; probe made no hardware writes\n");
	return 0;
}

static const struct of_device_id j208_gate_matches[] = {
	{ .compatible = "orchard,j208-wled-gate" },
	{}
};
MODULE_DEVICE_TABLE(of, j208_gate_matches);

static struct platform_driver j208_gate_driver = {
	.probe = j208_gate_probe,
	.driver = {
		.name = "j208-wled-gate",
		.of_match_table = j208_gate_matches,
	},
};
module_platform_driver(j208_gate_driver);
MODULE_DESCRIPTION("J208 RAM-only WLED handoff-state gate test");
MODULE_VERSION("handoff-state-v1");
MODULE_LICENSE("GPL");

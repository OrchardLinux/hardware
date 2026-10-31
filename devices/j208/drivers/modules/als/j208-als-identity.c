// SPDX-License-Identifier: GPL-2.0-only
/* One original-board ALS identity/data observation, no sensor control writes.
 * The existing i2c-apple driver owns controller reset, clocks and transfers.
 * Retain the overlay through this RAM boot; never race adapter teardown.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>

#include "i2c3-dtbo.h"

static int activation_rc = -EINPROGRESS;
static int identity_rc[2] = { -EINPROGRESS, -EINPROGRESS };
static int status_rc[2] = { -EINPROGRESS, -EINPROGRESS };
static int data_rc[2] = { -EINPROGRESS, -EINPROGRESS };
static unsigned int identity[2], status[2];
static unsigned char data0[8], data1[8];
static int overlay_id;
module_param(activation_rc, int, 0400);
module_param_array(identity_rc, int, NULL, 0400);
module_param_array(status_rc, int, NULL, 0400);
module_param_array(data_rc, int, NULL, 0400);
module_param_array(identity, uint, NULL, 0400);
module_param_array(status, uint, NULL, 0400);
module_param_array(data0, byte, NULL, 0400);
module_param_array(data1, byte, NULL, 0400);

static int read_command(struct i2c_adapter *adapter, u16 address, u8 command,
			u8 *value, u16 length)
{
	struct i2c_msg messages[] = {
		{ .addr = address, .len = 1, .buf = &command },
		{ .addr = address, .flags = I2C_M_RD, .len = length, .buf = value },
	};
	int ret = i2c_transfer(adapter, messages, ARRAY_SIZE(messages));

	return ret == ARRAY_SIZE(messages) ? 0 : ret < 0 ? ret : -EIO;
}

static int __init j208_als_init(void)
{
	static const u16 addresses[] = { 0x29, 0x39 };
	struct platform_device *pdev;
	struct i2c_adapter *adapter;
	struct device_node *node;
	struct resource resource;
	u8 value;
	int ret, i;

	if (!of_machine_is_compatible("apple,j208"))
		return -ENODEV;
	node = of_find_node_by_path("/soc/i2c@20a113000");
	if (!node)
		return -ENODEV;
	ret = of_address_to_resource(node, 0, &resource);
	if (ret || resource.start != 0x20a113000ULL ||
	    resource_size(&resource) != 0x1000 ||
	    !of_device_is_compatible(node, "apple,t8010-i2c") ||
	    of_device_is_available(node)) {
		ret = -EINVAL;
		goto put_node;
	}
	pdev = of_find_device_by_node(node);
	if (pdev) {
		put_device(&pdev->dev);
		ret = -EBUSY;
		goto put_node;
	}
	activation_rc = of_overlay_fdt_apply(j208_i2c3_dtbo,
			sizeof(j208_i2c3_dtbo), &overlay_id, NULL);
	/* A notifier may have created a controller even on failure. Retain it. */
	__module_get(THIS_MODULE);
	ret = 0;
	if (activation_rc)
		goto put_node;
	adapter = of_get_i2c_adapter_by_node(node);
	if (!adapter) {
		activation_rc = -ENODEV;
		goto put_node;
	}
	if (!i2c_check_functionality(adapter, I2C_FUNC_I2C)) {
		activation_rc = -EOPNOTSUPP;
		goto put_adapter;
	}
	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		value = 0;
		identity_rc[i] = read_command(adapter, addresses[i], 0x92, &value, 1);
		if (identity_rc[i])
			continue;
		identity[i] = value;
		if (((value | 8) & 0xfc) != 0x8c && (value & 0xfc) != 0xc8)
			continue;
		status_rc[i] = read_command(adapter, addresses[i], 0x93, &value, 1);
		if (status_rc[i])
			continue;
		status[i] = value;
		data_rc[i] = read_command(adapter, addresses[i], 0x94,
					 i ? data1 : data0, 8);
	}
	pr_info("J208_ALS observation complete: IDs rc=%d,%d value=%02x,%02x; sensor control writes=0\n",
		identity_rc[0], identity_rc[1], identity[0], identity[1]);
put_adapter:
	i2c_put_adapter(adapter);
put_node:
	of_node_put(node);
	return ret;
}

module_init(j208_als_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 one-shot ALS identity through the existing I2C3 controller");

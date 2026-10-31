// SPDX-License-Identifier: GPL-2.0-only
/* Read saved native configuration addresses from the two observed 0x8f parts.
 * Reuse the already active I2C3 adapter; no overlay or register data writes.
 */
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>

static int sample_rc[2] = { -EINPROGRESS, -EINPROGRESS };
static unsigned int identity[2], config0[5], config1[5];
module_param_array(sample_rc, int, NULL, 0400);
module_param_array(identity, uint, NULL, 0400);
module_param_array(config0, uint, NULL, 0400);
module_param_array(config1, uint, NULL, 0400);

static int read_byte(struct i2c_adapter *adapter, u16 address, u8 command, u8 *value)
{
	struct i2c_msg messages[] = {
		{ .addr = address, .len = 1, .buf = &command },
		{ .addr = address, .flags = I2C_M_RD, .len = 1, .buf = value },
	};
	int ret = i2c_transfer(adapter, messages, ARRAY_SIZE(messages));

	return ret == ARRAY_SIZE(messages) ? 0 : ret < 0 ? ret : -EIO;
}

static int __init j208_als_config_init(void)
{
	static const u16 addresses[] = { 0x29, 0x39 };
	static const u8 registers[] = { 0x80, 0x81, 0x8c, 0x8f, 0x90 };
	struct device_node *node;
	struct i2c_adapter *adapter;
	unsigned int *values;
	u8 value;
	int i, j;

	if (!of_machine_is_compatible("apple,j208"))
		return -ENODEV;
	node = of_find_node_by_path("/soc/i2c@20a113000");
	if (!node)
		return -ENODEV;
	adapter = of_get_i2c_adapter_by_node(node);
	of_node_put(node);
	if (!adapter)
		return -ENODEV;
	if (!i2c_check_functionality(adapter, I2C_FUNC_I2C)) {
		i2c_put_adapter(adapter);
		return -EOPNOTSUPP;
	}
	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		values = i ? config1 : config0;
		sample_rc[i] = read_byte(adapter, addresses[i], 0x92, &value);
		if (sample_rc[i])
			continue;
		identity[i] = value;
		if (value != 0x8f) {
			sample_rc[i] = -ENODEV;
			continue;
		}
		for (j = 0; j < ARRAY_SIZE(registers); j++) {
			sample_rc[i] = read_byte(adapter, addresses[i], registers[j], &value);
			if (sample_rc[i])
				break;
			values[j] = value;
		}
	}
	i2c_put_adapter(adapter);
	pr_info("J208_ALS_CONFIG rc=%d,%d enable=%02x,%02x; register data writes=0\n",
		sample_rc[0], sample_rc[1], config0[0], config1[0]);
	return 0;
}

static void __exit j208_als_config_exit(void) { }
module_init(j208_als_config_init);
module_exit(j208_als_config_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 read-only configuration of the two observed ALS devices");

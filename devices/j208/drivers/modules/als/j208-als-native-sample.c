// SPDX-License-Identifier: GPL-2.0-only
/* J208: three fresh conversion epochs per identified ALS, then exact disable.
 * GPIO0 is the source-verified native hard-disable mode; GPIO7 is Linux IRQ_OFF.
 * Both cached and uncached control fields must agree. No GPIO/IRQ writes.
 */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>

static int guard_rc = -EINPROGRESS, gpio_after_rc = -EINPROGRESS;
static int result_rc[2] = { -EINPROGRESS, -EINPROGRESS };
static int restore_rc[2] = { -EINPROGRESS, -EINPROGRESS };
static unsigned int pin_cached[2], pin_live[2], pin_after_cached[2], pin_after_live[2];
static unsigned int identity[2], attempted[2], valid_count[2], restored_count[2];
static unsigned int initial0[5], initial1[5], final0[5], final1[5];
static unsigned char reset0[3], reset1[3], status0[3], status1[3];
static unsigned char after0[3], after1[3], disabled0[3], disabled1[3];
static unsigned char control0[3], control1[3], aux0[3], aux1[3];
static unsigned char data0[24], data1[24];
static bool long_exposure, gain_16x;
module_param(long_exposure, bool, 0400);
module_param(gain_16x, bool, 0400);
module_param(guard_rc, int, 0400);
module_param(gpio_after_rc, int, 0400);
module_param_array(result_rc, int, NULL, 0400);
module_param_array(restore_rc, int, NULL, 0400);
module_param_array(pin_cached, uint, NULL, 0400);
module_param_array(pin_live, uint, NULL, 0400);
module_param_array(pin_after_cached, uint, NULL, 0400);
module_param_array(pin_after_live, uint, NULL, 0400);
module_param_array(identity, uint, NULL, 0400);
module_param_array(attempted, uint, NULL, 0400);
module_param_array(valid_count, uint, NULL, 0400);
module_param_array(restored_count, uint, NULL, 0400);
module_param_array(initial0, uint, NULL, 0400);
module_param_array(initial1, uint, NULL, 0400);
module_param_array(final0, uint, NULL, 0400);
module_param_array(final1, uint, NULL, 0400);
module_param_array(reset0, byte, NULL, 0400);
module_param_array(reset1, byte, NULL, 0400);
module_param_array(status0, byte, NULL, 0400);
module_param_array(status1, byte, NULL, 0400);
module_param_array(after0, byte, NULL, 0400);
module_param_array(after1, byte, NULL, 0400);
module_param_array(disabled0, byte, NULL, 0400);
module_param_array(disabled1, byte, NULL, 0400);
module_param_array(control0, byte, NULL, 0400);
module_param_array(control1, byte, NULL, 0400);
module_param_array(aux0, byte, NULL, 0400);
module_param_array(aux1, byte, NULL, 0400);
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

static int write_command(struct i2c_adapter *adapter, u16 address, u8 command, u8 value)
{
	u8 bytes[] = { command, value };
	struct i2c_msg message = { .addr = address, .len = sizeof(bytes), .buf = bytes };
	int ret = i2c_transfer(adapter, &message, 1);

	return ret == 1 ? 0 : ret < 0 ? ret : -EIO;
}

static int read_config(struct i2c_adapter *adapter, u16 address, unsigned int *values)
{
	static const u8 registers[] = { 0x80, 0x81, 0x8c, 0x8f, 0x90 };
	u8 value;
	int i, ret;

	for (i = 0; i < ARRAY_SIZE(registers); i++) {
		ret = read_command(adapter, address, registers[i], &value, 1);
		if (ret)
			return ret;
		values[i] = value;
	}
	return 0;
}

static int enable_sensor(struct i2c_adapter *adapter, u16 address)
{
	/* Native gain table index 4: CONTROL=2, AUX=0, nominal 16x. */
	const u8 writes[][2] = {
		{ 0x81, long_exposure ? 0xdd : 0xff },
		{ 0x8f, gain_16x ? 2 : 0 }, { 0x90, 0 }, { 0x8c, 0 }, { 0x80, 0x13 },
	};
	int i, ret;

	if (gain_16x) {
		ret = write_command(adapter, address, 0x80, 0);
		if (ret)
			return ret;
		ret = write_command(adapter, address, 0xe7, 0);
		if (ret)
			return ret;
	}
	for (i = 0; i < ARRAY_SIZE(writes); i++) {
		ret = write_command(adapter, address, writes[i][0], writes[i][1]);
		if (ret)
			return ret;
	}
	return 0;
}

static int disable_sensor(struct i2c_adapter *adapter, u16 address)
{
	int first = write_command(adapter, address, 0x80, 0);
	int second = write_command(adapter, address, 0xe7, 0);
	int config = long_exposure ? write_command(adapter, address, 0x81, 0xff) : 0;
	int gain = gain_16x ? write_command(adapter, address, 0x8f, 0) : 0;

	return first ? first : second ? second : config ? config : gain;
}

static int wait_ready(struct i2c_adapter *adapter, u16 address, u8 *status)
{
	int poll, ret;

	/* Native interval 2780us; require fresh valid+event, never a fixed delay alone.
	 * The public TCS3490 AVALID rule is an extra conservative qualification
	 * for the original Apple's 0x8f family variant, not a part-number assertion.
	 */
	for (poll = 0; poll < (long_exposure ? 60 : 20); poll++) {
		msleep(5);
		ret = read_command(adapter, address, 0x93, status, 1);
		if (ret)
			return ret;
		if (*status & 0x80)
			return -ERANGE;
		if ((*status & 0x11) == 0x11)
			return 0;
	}
	return -ETIMEDOUT;
}

static void sample_sensor(struct i2c_adapter *adapter, u16 address, int index)
{
	u8 *reset = index ? reset1 : reset0, *status = index ? status1 : status0;
	u8 *after = index ? after1 : after0, *disabled = index ? disabled1 : disabled0;
	u8 *control = index ? control1 : control0, *aux = index ? aux1 : aux0;
	u8 *data = index ? data1 : data0;
	unsigned int *initial = index ? initial1 : initial0;
	unsigned int *final = index ? final1 : final0;
	u8 value;
	int i, channel, ret;

	result_rc[index] = 0;
	for (i = 0; i < 3; i++) {
		result_rc[index] = read_command(adapter, address, 0x93, &reset[i], 1);
		if (!result_rc[index] && (reset[i] & 0x91))
			result_rc[index] = -EBUSY;
		if (result_rc[index])
			break;
		attempted[index]++;
		result_rc[index] = enable_sensor(adapter, address);
		if (!result_rc[index]) {
			result_rc[index] = read_command(adapter, address, 0x80, &value, 1);
			if (!result_rc[index] && value != 0x13)
				result_rc[index] = -EIO;
		}
		if (!result_rc[index]) {
			result_rc[index] = read_command(adapter, address, 0x81, &value, 1);
			if (!result_rc[index] && value != (long_exposure ? 0xdd : 0xff))
				result_rc[index] = -EIO;
		}
		if (!result_rc[index]) {
			result_rc[index] = read_command(adapter, address, 0x8f, &control[i], 1);
			if (!result_rc[index] && control[i] != (gain_16x ? 2 : 0))
				result_rc[index] = -EIO;
		}
		if (!result_rc[index]) {
			result_rc[index] = read_command(adapter, address, 0x90, &aux[i], 1);
			if (!result_rc[index] && aux[i] != 0)
				result_rc[index] = -EIO;
		}
		if (!result_rc[index])
			result_rc[index] = wait_ready(adapter, address, &status[i]);
		if (!result_rc[index])
			result_rc[index] = read_command(adapter, address, 0x94, data+i*8, 8);
		if (!result_rc[index]) {
			/* At ATIME=255/221 the public limit is 1024/35840; reject saturation
			 * conservatively even if the native ASAT flag is clear.
			 */
			for (channel = 0; channel < 4; channel++)
				if ((data[i*8+channel*2] |
				     data[i*8+channel*2+1] << 8) >= (long_exposure ? 35840 : 1024))
					result_rc[index] = -ERANGE;
		}
		if (!result_rc[index]) {
			result_rc[index] = read_command(adapter, address, 0x93, &after[i], 1);
			if (!result_rc[index] && (after[i] & 0x91) != 0x11)
				result_rc[index] = -EIO;
		}
		if (!result_rc[index])
			valid_count[index]++;
		/* Unconditional after any attempted control write, including failed ones. */
		restore_rc[index] = disable_sensor(adapter, address);
		msleep(4);
		ret = read_config(adapter, address, final);
		if (!restore_rc[index])
			restore_rc[index] = ret ? ret :
				memcmp(initial, final, 5 * sizeof(*initial)) ? -EIO : 0;
		ret = read_command(adapter, address, 0x93, &disabled[i], 1);
		if (!restore_rc[index])
			restore_rc[index] = ret ? ret : (disabled[i] & 0x91) ? -EIO : 0;
		if (!restore_rc[index])
			restored_count[index]++;
		if (result_rc[index] || restore_rc[index])
			break;
	}
}

static int irq_guards(unsigned int *cached, unsigned int *live)
{
	static const unsigned int pins[] = { 125, 105 };
	struct device_node *node;
	struct platform_device *pdev;
	struct regmap *map;
	unsigned int control;
	int i, ret = -ENODEV;

	node = of_find_node_by_path("/soc/pinctrl@20f100000");
	if (!node)
		return ret;
	pdev = of_find_device_by_node(node);
	of_node_put(node);
	if (!pdev)
		return ret;
	map = dev_get_regmap(&pdev->dev, NULL);
	if (!map)
		goto put_device;
	for (i = 0; i < ARRAY_SIZE(pins); i++) {
		ret = regmap_read(map, 4 * pins[i], &cached[i]);
		if (!ret)
			ret = regmap_read_bypassed(map, 4 * pins[i], &live[i]);
		if (ret)
			break;
		control = live[i] & 0x6e;
		if ((cached[i] & 0x6e) != control || (control != 0 && control != 0x0e)) {
			ret = -EBUSY;
			break;
		}
	}
put_device:
	put_device(&pdev->dev);
	return ret;
}

static int __init j208_als_native_sample_init(void)
{
	static const u16 addresses[] = { 0x29, 0x39 };
	static const unsigned int expected[] = { 0, 255, 0, 0, 0 };
	struct i2c_client *clients[2] = { NULL, NULL };
	struct device_node *node;
	struct i2c_adapter *adapter;
	unsigned int *config;
	u8 value;
	int i;

	if (!of_machine_is_compatible("apple,j208"))
		return -ENODEV;
	if (gain_16x && !long_exposure)
		return -EINVAL;
	guard_rc = irq_guards(pin_cached, pin_live);
	if (guard_rc)
		return 0;
	node = of_find_node_by_path("/soc/i2c@20a113000");
	if (!node)
		return -ENODEV;
	adapter = of_get_i2c_adapter_by_node(node);
	of_node_put(node);
	if (!adapter)
		return -ENODEV;
	if (!i2c_check_functionality(adapter, I2C_FUNC_I2C)) {
		guard_rc = -EOPNOTSUPP;
		goto release;
	}
	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		clients[i] = i2c_new_dummy_device(adapter, addresses[i]);
		if (IS_ERR(clients[i])) {
			guard_rc = PTR_ERR(clients[i]);
			clients[i] = NULL;
			goto release;
		}
		guard_rc = read_command(adapter, addresses[i], 0x92, &value, 1);
		if (guard_rc)
			goto release;
		identity[i] = value;
		if (value != 0x8f) {
			guard_rc = -ENODEV;
			goto release;
		}
		config = i ? initial1 : initial0;
		guard_rc = read_config(adapter, addresses[i], config);
		if (guard_rc)
			goto release;
		if (memcmp(config, expected, sizeof(expected))) {
			guard_rc = -EINVAL;
			goto release;
		}
		guard_rc = read_command(adapter, addresses[i], 0x93, &value, 1);
		if (guard_rc)
			goto release;
		if (value != 4) {
			guard_rc = -EBUSY;
			goto release;
		}
	}
	for (i = 0; i < ARRAY_SIZE(addresses); i++) {
		sample_sensor(adapter, addresses[i], i);
		if (result_rc[i] || restore_rc[i])
			break;
	}
	gpio_after_rc = irq_guards(pin_after_cached, pin_after_live);
	for (i = 0; !gpio_after_rc && i < ARRAY_SIZE(pin_live); i++)
		if (((pin_after_cached[i] ^ pin_cached[i]) & ~1U) ||
		    ((pin_after_live[i] ^ pin_live[i]) & ~1U))
			gpio_after_rc = -EIO;
release:
	for (i = ARRAY_SIZE(clients)-1; i >= 0; i--)
		if (clients[i])
			i2c_unregister_device(clients[i]);
	i2c_put_adapter(adapter);
	pr_info("J208_ALS_NATIVE guard=%d rc=%d,%d restore=%d,%d valid=%u,%u GPIO=%d\n",
		guard_rc, result_rc[0], result_rc[1], restore_rc[0], restore_rc[1],
		valid_count[0], valid_count[1], gpio_after_rc);
	return 0;
}

static void __exit j208_als_native_sample_exit(void) { }
module_init(j208_als_native_sample_init);
module_exit(j208_als_native_sample_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 fresh ALS conversion epochs with native GPIO hard-disable guards");

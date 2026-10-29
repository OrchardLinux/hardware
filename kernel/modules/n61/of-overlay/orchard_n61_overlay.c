// SPDX-License-Identifier: GPL-2.0-only
/* Explicit N61 development overlay; board support belongs in the native DTB. */
#include <linux/errno.h>
#include <linux/firmware.h>
#include <linux/i2c.h>
#include <linux/init.h>
#include <linux/mm.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/unaligned.h>

#define N61_OVERLAY "apple/n61-touch-development.dtbo"
#define N61_SPI_PATH "/soc/spi@20a088000"
#define N61_CORE_PATH "/soc/i2c@20a110000/pmic@74/regulator-touch-core"
#define N61_OVERLAY_MAX_SIZE (64 * 1024)

static const struct firmware *n61_overlay;
static int n61_overlay_id;

/* A live OF child notification cannot locate an I2C client as its parent. */
static int n61_overlay_attach_core(void)
{
	struct device_node *node, *parent;
	struct platform_device *child;
	struct i2c_client *pmic;
	int error = 0;

	node = of_find_node_by_path(N61_CORE_PATH);
	if (!node)
		return 0;
	if (!of_device_is_compatible(node, "apple,d2186-touch-core-regulator")) {
		error = -EINVAL;
		goto put_node;
	}
	parent = of_get_parent(node);
	pmic = of_find_i2c_device_by_node(parent);
	of_node_put(parent);
	if (!pmic) {
		error = -ENODEV;
		goto put_node;
	}
	if (!dev_get_regmap(&pmic->dev, NULL)) {
		error = -ENODEV;
		goto put_pmic;
	}
	child = of_find_device_by_node(node);
	if (!child) {
		error = -ENODEV;
		goto put_pmic;
	}
	device_lock(&child->dev);
	if (child->dev.parent != &pmic->dev) {
		if (child->dev.driver)
			error = -EBUSY;
		else
			error = device_move(&child->dev, &pmic->dev,
					    DPM_ORDER_DEV_AFTER_PARENT);
	}
	device_unlock(&child->dev);
	if (!error) {
		error = device_attach(&child->dev);
		if (error == 1)
			error = 0;
		else if (!error)
			error = -ENODEV;
	}
	put_device(&child->dev);
put_pmic:
	put_device(&pmic->dev);
put_node:
	of_node_put(node);
	return error;
}

static int __init orchard_n61_overlay_init(void)
{
	struct device_node *existing;
	int error;

	if (PAGE_SIZE != 4096 || !of_machine_is_compatible("apple,n61"))
		return -ENODEV;

	existing = of_find_node_by_path(N61_SPI_PATH);
	if (existing) {
		of_node_put(existing);
		return -EEXIST;
	}
	existing = of_find_node_by_path(N61_CORE_PATH);
	if (existing) {
		of_node_put(existing);
		return -EEXIST;
	}

	error = request_firmware_direct(&n61_overlay, N61_OVERLAY, NULL);
	if (error)
		return error;
	if (n61_overlay->size < 40 ||
	    n61_overlay->size > N61_OVERLAY_MAX_SIZE ||
	    get_unaligned_be32(n61_overlay->data) != 0xd00dfeed ||
	    get_unaligned_be32(n61_overlay->data + 4) != n61_overlay->size) {
		error = -EINVAL;
		goto release;
	}

	error = of_overlay_fdt_apply(n61_overlay->data, n61_overlay->size,
				     &n61_overlay_id, NULL);
	if (!error)
		error = n61_overlay_attach_core();
	if (error) {
		if (n61_overlay_id) {
			int cleanup = of_overlay_remove(&n61_overlay_id);

			if (cleanup) {
				pr_err("N61 overlay cleanup failed: %d; inspect before retry\n",
				       cleanup);
				/* Retain backing bytes if a partial overlay remains. */
				return error;
			}
		}
		goto release;
	}

	pr_info("N61 development touch overlay applied: %d\n", n61_overlay_id);
	return 0;

release:
	release_firmware(n61_overlay);
	n61_overlay = NULL;
	return error;
}

static void __exit orchard_n61_overlay_exit(void)
{
	int error = of_overlay_remove(&n61_overlay_id);

	if (error) {
		pr_err("N61 overlay removal failed: %d; inspect before retry\n", error);
		return;
	}
	release_firmware(n61_overlay);
}

module_init(orchard_n61_overlay_init);
module_exit(orchard_n61_overlay_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Explicit N61 touch development overlay for a matched 4K kernel");

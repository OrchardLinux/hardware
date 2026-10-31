// SPDX-License-Identifier: GPL-2.0-only
/* One RAM-only UART5/HDQ activation for J208. No raw MMIO or charger writes.
 * The boot DT has an existing UART5 domain without a phandle. Attach a normal
 * runtime-PM consumer by its provider node rather than changing live phandles.
 * Keep the overlay and domain for this boot: samsung_tty's remove path calls
 * uart_unregister_driver(), which would also disturb the Bluetooth UART.
 */
#include <linux/device.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/pm_domain.h>
#include <linux/pm_runtime.h>

#include "battery-dtbo.h"

static struct device *domain_consumer;
static int overlay_id;
static int overlay_status = -EINPROGRESS;
static bool domain_held;
module_param(overlay_status, int, 0400);
module_param(domain_held, bool, 0400);

static int __init j208_battery_init(void)
{
	struct of_phandle_args domain = {};
	struct device_node *uart;
	u32 reg[2];
	int ret;

	if (!of_machine_is_compatible("apple,j208"))
		return -ENODEV;
	uart = of_find_node_by_path("/soc/serial@20a0d4000");
	if (uart) {
		of_node_put(uart);
		return -EBUSY;
	}
	domain.np = of_find_node_by_path(
		"/soc/power-management@20e000000/power-controller@80208");
	if (!domain.np)
		return -ENODEV;
	ret = of_property_read_u32_array(domain.np, "reg", reg, 2);
	if (ret || reg[0] != 0x80208 || reg[1] != 4 ||
	    !of_device_is_compatible(domain.np, "apple,pmgr-pwrstate")) {
		of_node_put(domain.np);
		return -EINVAL;
	}

	domain_consumer = root_device_register("j208-battery-domain");
	if (IS_ERR(domain_consumer)) {
		of_node_put(domain.np);
		return PTR_ERR(domain_consumer);
	}
	ret = of_genpd_add_device(&domain, domain_consumer);
	of_node_put(domain.np);
	if (ret)
		goto unregister;
	pm_runtime_enable(domain_consumer);
	ret = pm_runtime_resume_and_get(domain_consumer);
	if (ret < 0)
		goto detach;
	domain_held = true;
	pr_info("J208_BATTERY domain=uart5 held via genpd; applying embedded DTBO\n");

	overlay_status = of_overlay_fdt_apply(j208_battery_dtbo,
				 sizeof(j208_battery_dtbo), &overlay_id, NULL);
	pr_info("J208_BATTERY overlay=%d result=%d; no charging configuration writes\n",
		overlay_id, overlay_status);
	/* Even a failed overlay notifier may have created a UART. Do not remove
	 * it or power it off underneath active serial consumers. Retain ownership.
	 * The host records a nonzero overlay_status as failure and does not retry.
	 */
	__module_get(THIS_MODULE);
	return 0;

detach:
	pm_runtime_disable(domain_consumer);
	pm_genpd_remove_device(domain_consumer);
unregister:
	root_device_unregister(domain_consumer);
	return ret;
}

/* Deliberately no exit routine: this diagnostic is retained until RAM reboot. */
module_init(j208_battery_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 RAM UART5 domain and read-only HDQ battery activation");

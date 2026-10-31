// SPDX-License-Identifier: GPL-2.0-only
/* J208 RAM-only register-profile quirk. The pack does not answer NAC (0x0c),
 * but answers RemainingCapacity (0x10). Mark NAC unavailable in a private
 * copy so bq27xxx's existing charge_now fallback selects RemainingCapacity.
 * No change to the shared profile, gauge registers or charging parameters.
 */
#include <linux/module.h>
#include <linux/of.h>
#include <linux/power/bq27xxx_battery.h>

#include "profile-layout.h"

static u8 j208_regs[BQ27XXX_REG_MAX];
static bool applied;
module_param(applied, bool, 0400);

static int __init j208_battery_profile_init(void)
{
	struct power_supply *psy;
	struct bq27xxx_device_info *di;
	int nac, remaining, full, soc, ret = -EINVAL;

	if (!of_machine_is_compatible("apple,j208"))
		return -ENODEV;
	psy = power_supply_get_by_name("battery");
	if (!psy)
		return -ENODEV;
	if (!psy->desc || strcmp(psy->desc->name, "battery") ||
	    !psy->dev.parent || !of_device_is_compatible(
		psy->dev.parent->of_node, "ti,bq27540-hdq"))
		goto put;
	di = power_supply_get_drvdata(psy);
	if (!di || di->chip != BQ27541 || !di->bus.read || di->bus.write ||
	    di->bus.write_bulk)
		goto put;
	mutex_lock(&di->lock);
	if (memcmp(di->regs, j208_expected_regs, sizeof(j208_regs)))
		goto unlock;
	nac = di->bus.read(di, 0x0c, false);
	remaining = di->bus.read(di, 0x10, false);
	full = di->bus.read(di, 0x12, false);
	soc = di->bus.read(di, 0x2c, false);
	if (nac != -EIO || full < 4000 || full > 11000 || remaining < 0 ||
	    remaining > full + 200 || soc < 0 || soc > 100 ||
	    abs(remaining * 100 - soc * full) > full * 5)
		goto unlock;
	memcpy(j208_regs, di->regs, sizeof(j208_regs));
	j208_regs[BQ27XXX_REG_NAC] = 0xff;
	/* Both the original table and this private replacement remain allocated
	 * for this boot. Readers outside di->lock can safely finish on either.
	 */
	__module_get(THIS_MODULE);
	smp_store_release(&di->regs, j208_regs);
	applied = true;
	ret = 0;
	pr_info("J208_BATTERY_PROFILE charge_now uses measured RemainingCapacity; gauge writes=0\n");
unlock:
	mutex_unlock(&di->lock);
	if (!ret)
		power_supply_changed(psy);
put:
	power_supply_put(psy);
	return ret;
}

/* The device's profile points into this module; retain it until reboot. */
module_init(j208_battery_profile_init);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J208 private RAM battery profile with RemainingCapacity fallback");

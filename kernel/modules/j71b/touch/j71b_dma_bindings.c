// SPDX-License-Identifier: GPL-2.0-or-later
/* Reversible OF changeset: DMA, experiment flags and 4MHz maximum. No MMIO, GPIO or DMA operations.
 * Misc file ownership + self-reference prevent removal while changes are live.
 */
#include <linux/fs.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/of_platform.h>
#include <linux/platform_device.h>
#include <linux/uaccess.h>

static DEFINE_MUTEX(control_lock);
static struct platform_device *spi_parent;
static struct device_node *spi_node, *chip_node, *sio_node;
static struct of_changeset changes;
static struct property *old_frequency;
enum { CLEAN, APPLIED, UNCERTAIN };
static int state;
module_param(state, int, 0444);
MODULE_PARM_DESC(state, "0 clean, 1 applied, 2 uncertain: do not bind/unload");

/* Called under control_lock and the SPI platform device lock. */
static int attach(void)
{
 u32 mapping[2], hz;
 int ret;
 if (spi_parent->dev.driver) return -EBUSY;
 if (state != CLEAN) return -EALREADY;
 if (of_find_property(spi_node,"dmas",NULL) ||
     of_find_property(spi_node,"dma-names",NULL) ||
     of_property_read_bool(chip_node,"apple,dma-reset-only") ||
     of_property_read_bool(chip_node,"apple,firmware-startup-only") || !sio_node->phandle)
  return -EINVAL;
 old_frequency=of_find_property(chip_node,"spi-max-frequency",NULL);
 if (!old_frequency || of_property_read_u32(chip_node,"spi-max-frequency",&hz) || hz!=500000)
  return -EINVAL;
 mapping[0]=sio_node->phandle;mapping[1]=26;
 of_changeset_init(&changes);
 ret=of_changeset_add_prop_u32_array(&changes,spi_node,"dmas",mapping,2);
 if (ret) goto before_apply;
 ret=of_changeset_add_prop_string(&changes,spi_node,"dma-names","tx");
 if (ret) goto before_apply;
 ret=of_changeset_add_prop_bool(&changes,chip_node,"apple,dma-reset-only");
 if (ret) goto before_apply;
 ret=of_changeset_add_prop_bool(&changes,chip_node,"apple,firmware-startup-only");
 if (ret) goto before_apply;
 ret=of_changeset_remove_property(&changes,chip_node,old_frequency);
 if (ret) goto before_apply;
 ret=of_changeset_add_prop_u32(&changes,chip_node,"spi-max-frequency",4000000);
 if (ret) goto before_apply;
 /* of_changeset_apply errors may leave unknown partial effects. Pin FIRST;
  * never destroy or silently revert an uncertain transaction.
  */
 __module_get(THIS_MODULE);
 state=UNCERTAIN;
 ret=of_changeset_apply(&changes);
 if (ret) {
  pr_crit("J71B_BINDINGS_UNCERTAIN apply=%d keep controller unbound; inspect\n",ret);
  return ret;
 }
 state=APPLIED;
 pr_info("J71B_BINDINGS_APPLIED runtime-phandle=%u endpoint=26 properties=5 max-hz=4000000\n",mapping[0]);
 return 0;
 before_apply:
 of_changeset_destroy(&changes);
 return ret;
}
static int detach(void)
{
 int ret;
 if (spi_parent->dev.driver) return -EBUSY;
 if (state != APPLIED) return -EUCLEAN;
 state=UNCERTAIN;
 ret=of_changeset_revert(&changes);
 if (ret || of_find_property(chip_node,"spi-max-frequency",NULL)!=old_frequency ||
     of_property_read_bool(chip_node,"apple,firmware-startup-only") ||
     of_find_property(spi_node,"dmas",NULL) ||
     of_find_property(spi_node,"dma-names",NULL) ||
     of_property_read_bool(chip_node,"apple,dma-reset-only")) {
  pr_crit("J71B_BINDINGS_UNCERTAIN revert=%d keep controller unbound; inspect\n",ret);
  return ret ?: -EUCLEAN;
 }
 of_changeset_destroy(&changes);
 state=CLEAN;
 pr_info("J71B_BINDINGS_RESTORED properties=5 original-frequency-restored\n");
 /* The currently open misc file STILL owns a module ref through write/close. */
 module_put(THIS_MODULE);
 return 0;
}
static ssize_t control_write(struct file *file,const char __user *buffer,size_t count,loff_t *pos)
{
 char command[7];
 int ret;
 if (count!=sizeof(command)) return -EINVAL;
 if (copy_from_user(command,buffer,count)) return -EFAULT;
 mutex_lock(&control_lock);
 device_lock(&spi_parent->dev);
 if (!memcmp(command,"attach\n",7)) ret=attach();
 else if (!memcmp(command,"detach\n",7)) ret=detach();
 else ret=-EINVAL;
 device_unlock(&spi_parent->dev);
 mutex_unlock(&control_lock);
 return ret ? ret : count;
}
static const struct file_operations control_fops={
 .owner=THIS_MODULE,
 .write=control_write,
};
static struct miscdevice control_device={
 .minor=MISC_DYNAMIC_MINOR,.name="j71b_dma_bindings",.fops=&control_fops,.mode=0600,
};
static int __init bindings_init(void)
{
 int ret=-ENODEV;
 if (!of_machine_is_compatible("apple,j71b")) return ret;
 spi_node=of_find_node_by_path("/soc/spi@20a08c000");
 chip_node=of_find_node_by_path("/soc/spi@20a08c000/touchscreen@0");
 sio_node=of_find_node_by_path("/soc/dma-controller@20ae00000");
 if (!spi_node||!chip_node||!sio_node ||
     !of_device_is_compatible(spi_node,"apple,t8010-spi") ||
     !of_device_is_compatible(chip_node,"apple,j71b-touchscreen-readiness") ||
     !of_device_is_compatible(sio_node,"apple,t8010-sio")) goto put_nodes;
 spi_parent=of_find_device_by_node(spi_node);
 if (!spi_parent) goto put_nodes;
 ret=misc_register(&control_device);
 if (!ret) return 0;
 put_device(&spi_parent->dev);
 put_nodes:
 of_node_put(spi_node);of_node_put(chip_node);of_node_put(sio_node);
 return ret;
}
static void __exit bindings_exit(void)
{
 /* Normal unload cannot reach this with APPLIED/UNCERTAIN self-reference. */
 WARN_ON(state!=CLEAN);
 misc_deregister(&control_device);
 put_device(&spi_parent->dev);
 of_node_put(spi_node);of_node_put(chip_node);of_node_put(sio_node);
}
module_init(bindings_init);
module_exit(bindings_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J71b reversible SPI DMA bindings; unbound controller required");

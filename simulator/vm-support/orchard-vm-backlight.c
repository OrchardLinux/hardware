// VM-only substitute for the J71b backlight hardware, not part of the product image.
#include <linux/backlight.h>
#include <linux/module.h>
#include <linux/platform_device.h>

static struct platform_device *device;
static struct backlight_device *backlight;

static int vm_update_status(struct backlight_device *bd)
{
    // The VM bridge reads the real backlight class value and informs the viewer.
    return 0;
}

static int vm_get_brightness(struct backlight_device *bd)
{
    return bd->props.brightness;
}

static const struct backlight_ops vm_ops = {
    .update_status = vm_update_status,
    .get_brightness = vm_get_brightness,
};

static int __init vm_backlight_init(void)
{
    struct backlight_properties props = {
        .type = BACKLIGHT_RAW,
        .max_brightness = 1023,
        .brightness = 731,
    };
    device = platform_device_register_simple("orchard-vm-backlight", -1, NULL, 0);
    if (IS_ERR(device)) return PTR_ERR(device);
    backlight = backlight_device_register("20e200080.backlight", &device->dev,
                                         NULL, &vm_ops, &props);
    if (IS_ERR(backlight)) {
        int error = PTR_ERR(backlight);
        platform_device_unregister(device);
        return error;
    }
    return 0;
}

static void __exit vm_backlight_exit(void)
{
    backlight_device_unregister(backlight);
    platform_device_unregister(device);
}
module_init(vm_backlight_init);
module_exit(vm_backlight_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Virtual backlight for the Orchard simulator");

# J208 drivers

These are the drivers that ran on the iPad Pro 10.5 RAM desktop, kernel
`7.3.0-rc1-orchard-j208`
(`1c6c882ca6696925bfa04802a25c09cc138b178f340484dadbb50f61c403771e`).

`drivers/patches` are diffs against Hoolock `6831bc701` after the old J71b
patch series. `drivers/modules` are the out-of-tree modules from those
sessions. `ram-kernel.config` is that kernel's resolved configuration, with
internal NVMe left off.

`kernel/series` does not apply these patches. The current apple-16k series
starts at torvalds v7.3-rc1, and these diffs do not match those files. They
are kept here so the verified source is in the checkout. `./kernel/build j208`
also has no `t8011-j208.dtb` yet.

Included: touch reports, PCIe Wi-Fi port 2 and the WPA2 profile, the
D2400/D2401 rails, the backlight gate and bounded brightness, UART5/HDQ
battery readout, and both ambient-light sensors.

Not included: audio, GPU acceleration, charging, internal storage, and
device firmware.

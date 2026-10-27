# J71b device tree

The J71b kernel build produces `t8010-j71b-devbase.dtb`. The adjacent `build`
recipe applies the PCIe/Wi-Fi, Wi-Fi alias, battery, Bluetooth and GPIO power
overlays in order, followed by the public charging-source overlay, then writes
the final DTB to its explicit `--output` path. Charging remains the conservative
425 mA lease unless `j71b_charge_policy` is loaded with `source_aware=1` and
the private per-unit calibration and hardware guards pass. See
[`../../modules/j71b/power/README.md`](../../modules/j71b/power/README.md).
`kernel/build j71b --output DIRECTORY` invokes this recipe and includes the
result as `DIRECTORY/j71b.dtb`.

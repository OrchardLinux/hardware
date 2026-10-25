# J71b device tree

The J71b kernel build produces `t8010-j71b-devbase.dtb`. The adjacent `build`
recipe applies the PCIe/Wi-Fi, Wi-Fi alias, battery, Bluetooth and GPIO power
overlays in order, then writes the final DTB to its explicit `--output` path.
`kernel/build j71b --output DIRECTORY` invokes this recipe and includes the
result as `DIRECTORY/j71b.dtb`.

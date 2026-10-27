# Kernel configuration

`config` is the complete, resolved configuration used to build the J71t
kernel. It is a version-controlled build input despite its Kconfig-generated
“DO NOT EDIT” header.

## Origin

The starting point is the 16K-page HoolockLinux configuration (`config_16k`,
as for J71b) plus the linux-a9 workstream fragments for PCIe/NVMe, Wi-Fi,
Bluetooth, touch, audio, sensors, power and USB, as merged for the linux-a9
`a9/release` integration kernel. Options for code that is not part of
`../../patches/j71t/` (GPU, video decoder, SEP, AOP and diagnostics) no
longer exist and dropped out on resolution.

It was resolved for the patched kernel with Debian Clang/LLVM 19.1.7 (the
toolchain in `../../Containerfile`), after these Orchard-specific changes:

- `CONFIG_LOCALVERSION_AUTO` is disabled; the build script supplies
  `-orchard-j71t`.
- Drivers that read private firmware or calibration when they probe are
  modules, because Orchard delivers that data after boot:
  `CONFIG_TOUCHSCREEN_APPLE_N1=m` (N1 image and MtCl),
  `CONFIG_BT_HCIUART=m` (patchram and Bluetooth address),
  `CONFIG_APPLE_D2365_CHARGER=m` (CBAT input-limit calibration),
  `CONFIG_APPLE_SIO=m` (SmartIO firmware, used by audio) and
  `CONFIG_TSL2583=m` (LSCI ambient-light calibration). Recovery loads the
  first three in `runtime/j71t/phase2-hardware`; the installed system's udev
  loads the rest.
- `CONFIG_APPLE_A9_TUNING` is disabled, so the opt-in overclocking code is not
  built. CPU frequency stays on the stock 300–1800 MHz table.
- `CONFIG_MODULE_COMPRESS` is disabled, as for J71b: modules are plain `.ko`
  files for recovery's BusyBox `modprobe`.

## Important choices

- `CONFIG_ARM64_16K_PAGES=y`: 16 KiB kernel pages.
- `CONFIG_MTD_PHRAM=y`: the Apple Device Tree that m1n1 exposes, read by
  recovery's `capture-adt` and device identity.
- `CONFIG_PCIE_APPLE_H9P=y`, `CONFIG_BLK_DEV_NVME=y`,
  `CONFIG_NVME_PCI_READ_ONLY=y`, `CONFIG_NVME_PCI_WRITE_WINDOW=y`: built-in
  S8003 PCIe and ANS NVMe. NVMe stays read-only except for the boot-time
  `nvme.write_window`; namespaces 2-7 stay read-only regardless.
- `CONFIG_NVME_APPLE_ANS_EARLY_USB=y`: the PCIe host and ANS wait until
  `phase2-hardware` releases them.
- `CONFIG_BRCMFMAC=y`: Wi-Fi is built in. It probes only after the PCIe host
  is released, when the firmware mixin is already present.
- `CONFIG_DRM_SIMPLEDRM=y`: the display is the framebuffer m1n1 hands over;
  there is no GPU or native display-controller driver.

## How the file is generated

The build copies `config` into its output directory as `.config`, runs
`make LLVM=-19 ARCH=arm64 olddefconfig` and requires the result to be
byte-identical. To change it, edit a copy, resolve it the same way against the
patched source with the same toolchain, and commit the resolved file.

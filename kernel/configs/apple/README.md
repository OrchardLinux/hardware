# Kernel configuration

`config` is the complete, resolved configuration used to build the J71b
kernel. It is a version-controlled build input despite its Kconfig-generated
“DO NOT EDIT” header. Building does not require a separate configuration source.

## Origin

The starting configuration was `config_16k` from
[HoolockLinux/docs](https://github.com/HoolockLinux/docs), available in the
recorded source snapshot at commit
`23ebe1fbc375599221553a7e1815e5de182a6b42`:

https://github.com/HoolockLinux/docs/blob/23ebe1fbc375599221553a7e1815e5de182a6b42/config_16k

This is the configuration-source revision, distinct from the Linux source
revision pinned in `../../upstream.lock`.

The configuration was resolved for the patched ARM64 kernel using Debian
Clang/LLVM 19.1.7. Subsequent hardware bring-up added or adjusted options for:

- Apple T8010 touch clocks, SPI, SmartIO and DMA.
- Loadable PCIe and generic NVMe drivers, with the incompatible Apple NVMe
  driver disabled.
- Broadcom PCIe Wi-Fi, wireless configuration, RF-kill and board power control.
- Broadcom UART Bluetooth and serial-device support.
- Module loading/unloading, device-tree overlays and embedded configuration
  reporting.
- OverlayFS and SquashFS support.
- Additional development facilities, including I²C/SPI userspace interfaces,
  audio, media and PowerVR driver options. Enabled options do not establish
  working hardware support; in particular, they do not imply GPU acceleration.

The resulting configuration was retained through the working hardware builds.
For Omarchy A, `CONFIG_LOCALVERSION_AUTO` was disabled so Git state does not
add an automatic version suffix. The build script supplies the release suffix.

## Important choices

- `CONFIG_ARM64_16K_PAGES=y`: 16 KiB kernel pages.
- `CONFIG_MTD_PHRAM=y`: access to RAM-backed regions, including the Apple Device
  Tree exposed by m1n1.
- `CONFIG_SPI_APPLE=y` and `CONFIG_COMMON_CLK_APPLE_T8010_TOUCH=y`: built-in
  support needed by the touch transport.
- `CONFIG_APPLE_SIO=m` and `CONFIG_APPLE_SIO_DMA=m`: loadable SmartIO support.
- `CONFIG_TOUCHSCREEN_APPLE_Z2` is disabled: the current touchscreen runtime
  driver is supplied separately as `j71b_z2.ko`.

Additional modules are built against this same kernel configuration and its
symbol-version metadata. Some are built explicitly rather than selected through
Kconfig; consult `../../modules/j71b/` and `../../build-modules` for those sources.

## How the file is generated

Linux Kconfig produces the resolved `.config` from selected options, defaults,
dependencies and compiler capabilities. It does not detect the attached iPad.

The build copies `config` into its output directory as `.config`, then runs:

```sh
make -C /src O=/out LLVM=-19 ARCH=arm64 olddefconfig
```

`/src` and `/out` are the kernel source and output paths inside the build
container. The build compares the resolved result with the committed config and
stops if they differ.

To change configuration, use Kconfig tooling such as `menuconfig` or
`scripts/config`, resolve it with `olddefconfig`, and review the resulting diff
before updating the committed file. Preserve the pinned source/toolchain when
checking reproducibility.

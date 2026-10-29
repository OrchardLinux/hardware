# Orchard Hardware

Hardware support and image-building resources for Linux on Apple devices. This repository owns device descriptions, kernel patches/configuration, firmware recipes, recovery tooling and the device runtime. It does not depend on a desktop distribution or the host CLI.

## Supported hardware

Currently: iPad 6 Wi-Fi (`iPad7,5`, J71b), experimental. Boot is tethered and GPU acceleration is not available.

The iPhone 6 (`iPhone7,2`, N61AP) has an experimental 4 KiB RAM-boot recipe.
An earlier build reached a tethered desktop with touch. Its
[bring-up instructions](docs/n61-bringup.md) describe the sources and the
physical checks required for the reconciled build.

## Use

For installing and starting a published system, use the [Orchard CLI](https://github.com/OrchardLinux/cli). Its release requirements select compatible CLI and hardware wheels automatically.

For building components or another image, follow [BUILD.md](BUILD.md). All commands run from this repository or installed package resources; no sibling source checkout is needed.

- [Kernel producers](kernel/README.md)
- [J71b compatibility audit and validation limits](docs/j71b-compatibility-audit.md)
- [Recovery tools](boot/initramfs/recovery/README.md)
- [GitHub builds and release ordering](docs/ci.md)
- [Shared launcher build and publication](boot/quiet/README.md)
- [Device protocol](https://github.com/OrchardLinux/cli/blob/main/docs/device-protocol.md)

Public packages contain recipes and code, not private Apple firmware, calibration, device trees captured from hardware, or session credentials. Device-specific inputs are prepared separately on the user's host.

## License

See [LICENSE](LICENSE), [NOTICE](NOTICE), `LICENSES/`, and per-file SPDX identifiers for component licenses and upstream credits.

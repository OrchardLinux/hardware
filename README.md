# Orchard Hardware

Hardware support and image-building resources for Linux on Apple devices. This repository owns device descriptions, kernel patches/configuration, firmware recipes, recovery tooling and the device runtime. It does not depend on a desktop distribution or the host CLI.

## Supported hardware

Currently, all experimental, tethered and without GPU acceleration:

- iPad 6 Wi-Fi (`iPad7,5`, J71b, A10 T8010).
- iPad 5 Wi-Fi (`iPad6,11`, J71t, A9 S8003), tested on one unit. The Samsung S8000 variant (J71s) is not supported, and there is no shared launcher for J71t yet.

## Use

For installing and starting a published system, use the [Orchard CLI](https://github.com/OrchardLinux/cli). Its release requirements select compatible CLI and hardware wheels automatically.

For building components or another image, follow [BUILD.md](BUILD.md). All commands run from this repository or installed package resources; no sibling source checkout is needed.

- [Kernel producers](kernel/README.md)
- [Recovery tools](boot/initramfs/j71b/recovery/README.md) and [J71t recovery](boot/initramfs/j71t/recovery/README.md)
- [GitHub builds and release ordering](docs/ci.md)
- [Shared launcher build and publication](boot/quiet/README.md)
- [Device protocol](https://github.com/OrchardLinux/cli/blob/main/docs/device-protocol.md)

Public packages contain recipes and code, not private Apple firmware, calibration, device trees captured from hardware, or session credentials. Device-specific inputs are prepared separately on the user's host.

## License

See [LICENSE](LICENSE), [NOTICE](NOTICE), `LICENSES/`, and per-file SPDX identifiers for component licenses and upstream credits.

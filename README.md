# Orchard Hardware

Hardware support and image-building resources for Linux on Apple devices. This repository owns device descriptions, kernel patches/configuration, firmware recipes, recovery tooling and the device runtime. It does not depend on a desktop distribution or the host CLI.

## Hardware profiles

| Device | Profile | Boot status |
| --- | --- | --- |
| iPad 6 Wi-Fi (`iPad7,5`) | J71b | Experimental tethered Orchard launcher; software rendering |
| iPad mini 1 Wi-Fi (`iPad2,5`, P105AP) | P105 | Experimental manual bring-up through an existing jailbreak, with a host-served NFS root |

[P105 prerequisites and measured results](devices/p105/README.md) describe the tested A5/ARMv7 path. Its packaged runtime and pinned Cascadia preparation recipe support manual image assembly. Orchard CLI adapter integration and published launcher assets are separate work.

## Use

For installing and starting a published system, use the [Orchard CLI](https://github.com/OrchardLinux/cli). Its release requirements select compatible CLI and hardware wheels automatically.

For building components or another image, follow [BUILD.md](BUILD.md). All commands run from this repository or installed package resources; no sibling source checkout is needed.

- [Kernel producers](kernel/README.md)
- [J71b compatibility audit and validation limits](docs/j71b-compatibility-audit.md)
- [Recovery tools](boot/initramfs/j71b/recovery/README.md)
- [GitHub builds and release ordering](docs/ci.md)
- [Shared launcher build and publication](boot/quiet/README.md)
- [Device protocol](https://github.com/OrchardLinux/cli/blob/main/docs/device-protocol.md)

Public packages contain recipes and code, not private Apple firmware, calibration, device trees captured from hardware, or session credentials. Device-specific inputs are prepared separately on the user's host.

## License

See [LICENSE](LICENSE), [NOTICE](NOTICE), `LICENSES/`, and per-file SPDX identifiers for component licenses and upstream credits.

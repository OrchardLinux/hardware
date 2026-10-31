# Orchard

Linux on Apple devices. This repository holds everything a device needs and the
`orchard` tool that builds, boots and installs it: device descriptions, kernel
patches and configuration, m1n1 and Pongo patches, firmware recipes, the recovery
and device runtime, and the host side (USB transports, firmware conversion,
installation, launch). Distributions such as
[omarchy-a](https://github.com/OrchardLinux/omarchy-a) and
[minimal-linux](https://github.com/OrchardLinux/minimal-linux) build on it.

## Supported hardware

Currently: iPad 6 Wi-Fi (`iPad7,5`, J71b), experimental. Boot is tethered and GPU acceleration is not available.

The iPhone 6 (`iPhone7,2`, N61AP) has experimental tethered support. See the [bring-up record](docs/n61-bringup.md) for its tested features and remaining work.

J208 (`iPad7,4`, iPad Pro 10.5) keeps the RAM-verified touch, Wi-Fi, backlight, battery and light-sensor sources in [devices/j208](devices/j208/README.md). They are not wired into the current kernel series, and installation is disabled.

## Install a system

Follow the install instructions of a distribution, e.g. omarchy-a. Until the
installer that ships with each distribution release exists, published systems
install with the released Orchard 0.1.10 ([OrchardLinux/cli releases](https://github.com/OrchardLinux/cli/releases)).

To start an installed system again, connect the device in DFU mode and run
`orchard launch`. The host sends only Pongo and m1n1; m1n1 reads the boot entries
from the device's boot partition and starts the one you choose (`--entry ID` picks
one without asking). Any computer with Orchard can launch any installed system.

## Develop

`orchard` runs from a checkout of this repository: its hardware data and recipes
are the files beside `src/`. Put the launcher on your PATH once (it needs
[uv](https://docs.astral.sh/uv/)):

```sh
ln -sf "$PWD/bin/orchard" ~/.local/bin/orchard
```

It runs the checkout the current directory belongs to:

- inside an orchard checkout, that checkout, e.g. a worktree on your branch;
- elsewhere, the checkout named by the nearest `orchard.local.toml`
  (`orchard = "../orchard-gpu"`), e.g. in a distribution you work on;
- otherwise orchard's `main` branch, kept in `~/.cache/orchard/orchard-main` and
  updated before use.

`orchard.local.toml` belongs to one checkout and is never committed. It can also
choose your board (`device = "j71b"`); `~/.config/orchard/config.toml` does that
for all your checkouts. Nothing in a repository names a board.

Bring up a device or try a driver in a worktree of its own:

```sh
git worktree add ../orchard-gpu -b gpu-a10
cd ../orchard-gpu/examples/minimal-boot
orchard build --device j71b
orchard run
```

The [examples](examples/) are starting points: `minimal-boot` (a serial shell),
`charger-001` (a read-only observation), `hardware-check` (a report on what a device
offers). `orchard python ARGS` runs Python in the checkout's environment, e.g. a
distribution's own scripts or `orchard python -m unittest discover -s tests`. See
[BUILD.md](BUILD.md) for building components directly.

- [Kernel producers](kernel/README.md)
- [Device bring-up](docs/device-bring-up.md)
- [RAM builds and development](docs/ram-development.md)
- [Firmware recipes](docs/firmware-recipes.md)
- [Device operation protocol](docs/device-protocol.md)
- [Host adapter contract](docs/adapter-contract.md)
- [SSH lifecycle](docs/ssh-lifecycle.md)
- [Recovery tools](boot/initramfs/recovery/README.md)
- [Quiet boot patches](boot/quiet/README.md)
- [J71b compatibility audit and validation limits](docs/j71b-compatibility-audit.md)
- [GitHub builds](docs/ci.md)
- [Tooling design](docs/tooling.md)
- [Contributing](CONTRIBUTING.md)

The repository contains recipes and code, not private Apple firmware, calibration,
device trees captured from hardware, or session credentials. Device-specific inputs
are prepared on the user's host.

## License

See [LICENSE](LICENSE), [NOTICE](NOTICE), `LICENSES/`, and per-file SPDX identifiers for component licenses and upstream credits.

# Contributing

Thanks for helping. This repository is shared by every device Orchard supports,
so a change here can affect devices you don't have. The rules below exist so
that a reviewer can tell, for every line, why it is there and what it touches.

## What belongs here

This repository holds device descriptions, kernel patches and configuration,
m1n1 patches, firmware recipes, the boot and recovery initramfs, and the
`orchard` tool on the host (booting, installing, the host link, firmware
conversion) in `src/orchard`. A device change and the converter steps or host
support it needs go into one PR here. Desktops and distribution profiles
belong in [omarchy-a](https://github.com/OrchardLinux/omarchy-a); work that
also needs a distro change is one PR per repository, and each says which one
it needs and in which order they merge.

## One topic per pull request

Keep separate topics in separate PRs, each with a description of the problem
it solves:

- **A device:** its descriptor, firmware recipe, device tree, the driver
  changes it needs, the config options it needs and its documentation.
- **A boot or runtime path,** such as a new root filesystem source.
- **Shared build tooling:** `kernel/build`, `boot/m1n1/build`, `src/orchard/build.py`.
  These change every device's build.
- **CI.**

A device PR can depend on a tooling PR; say so and link it. Within a PR, split
commits by logical part. They don't need to be smaller than that.

## How pull requests land

We usually rework a pull request to fit the kernel structure and orchard's
layout before merging it: drivers in the kernel rather than in userspace,
patches in upstream style, device data in the device layer. The rework goes
onto your PR branch, your commits keep your authorship, and the PR is merged
on GitHub, so it shows as your contribution. If you'd rather we didn't touch
your branch, say so in the PR and we'll review it with you instead.

## Where kernel patches go

Patches are applied in the layer order of `kernel/series/<platform>`; within a
directory, in file-name order.

- `kernel/patches/drivers/<subsystem>/`: changes to drivers and their
  bindings, including new drivers. Use the subsystem directory that matches the
  kernel's (`spi`, `input`, `power`, …); add one when none fits.
- `kernel/patches/devices/<soc>/`: device trees only (`.dts`, `.dtsi`), named
  by SoC (`t7000`, `t8010`, …).
- `kernel/patches/hoolock/` and `kernel/patches/import/`: other trees we
  carry, exported unchanged. Their origin is recorded in `kernel/sources.toml`.
  Don't edit these; fix them with a later patch in `drivers/` or `devices/`.

Number a new patch with the next free number in its directory. Don't renumber
existing patches.

A driver patch filed under `devices/` hides that it changes behaviour on other
devices, which is why the split matters.

m1n1 patches go in `boot/m1n1/patches/orchard/`, the same way.

## Writing patches

Write patches as you would for upstream Linux:

- Subject in the subsystem's style (`Input: apple_z2: …`,
  `arm64: dts: apple: …`), one logical change per patch, and a body that says
  what changes and why.
- Hardware differences belong in match data and the device tree, selected by
  compatible. No checks for a specific machine inside shared code, and no
  module parameters for hardware behaviour.
- Bindings for new compatibles or properties go in
  `Documentation/devicetree/bindings`, in their own patch.
- The author is whoever wrote the code. If a patch derives from someone else's
  work, they stay the author, and an `[Orchard: from <repo> <commit>; …]` note
  above the sign-offs says where it comes from and what changed.

## Kernel configuration

`kernel/configs/<platform>/config` builds every device on that platform:
`apple-4k` covers A7, A8 and A8X, `apple-16k` covers A9 and later. For each
change, the commit message says which options change, which device or feature
needs them, and why. That covers options you turn off too, and options you
switch from module (`=m`) to built-in (`=y`).

Make a change in both configs unless it's only about the page size. Resolve
each with `olddefconfig` as described in `kernel/configs/apple-16k/README.md`,
and commit the resolved file.

## Bring-up material

Bring-up documentation is welcome: what you measured, traced or found out, in
`docs/bring-up/` or the device's docs. It's often the only record of how the
hardware works.

Tools you used to get there stay on your fork: overlay modules, development
device trees, debug switches. Main carries the final form that boots on the
device.

## Private data

Public files contain recipes and code, not Apple firmware, calibration data,
device trees dumped from a device, or credentials. Firmware is extracted on the
user's host from the recipe in `devices/<device>/firmware.json`.

## Testing

- Run the unit tests: `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v`.
- Test the exact revision you submit on the device, and say in the PR how: the
  command, the image, and what worked and what didn't.
- If you change a shared driver or config, say which other devices use it.
  We test those we have (currently the iPad 6, J71b) before merging.

Unit tests don't show that a device boots. A device feature is `working` in its
descriptor only after it was seen working on the device.

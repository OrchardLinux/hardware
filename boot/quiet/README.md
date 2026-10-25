# Black-screen shared launch

Shared discovery cannot know the installed distro before booting and inspecting
the device. Its Pongo and m1n1 components therefore clear the screen to black and
disable framebuffer text only. USB and serial diagnostics remain available.
Distro installation boot components are unchanged.

## Build

Requires macOS with Xcode and an iPhoneOS SDK for Pongo, Git, make, a native C toolchain, and Podman for m1n1. Install the hardware package as described in [BUILD.md](../../BUILD.md).

From the hardware repository:

```sh
python boot/quiet/build.py \
  --output out/quiet-boot/BUILD_ID --work-dir out/quiet-work/BUILD_ID
```

This builds the pinned Pongo/newlib and hardware-supported m1n1 sources in isolated
worktrees. It does not access a device. Use a new output directory; `--resume`
reuses only matching pinned, patched worktrees.

Pin the resulting `m1n1.bin` and `pongo.bin` as the loader and Pongo inputs for
`python -m orchard_hardware.image.launcher`. Supply the generated
`--quiet-boot-receipt out/quiet-boot/BUILD_ID/quiet-boot.json`. The launcher producer
checks both component identities and the current quiet-patch digests, refusing
accidental reuse of distro-branded assets.

## Publish

Build the launcher with its existing root compatibility inputs and an immutable
`--public-base-url https://github.com/OrchardLinux/hardware/releases/download/TAG`.
The output includes a local `boot.toml` and public `j71b-launcher.boot.toml` with
exact HTTPS URLs and content hashes. Publish `j71b-launcher.boot.toml`,
`recovery.bin`, and `pongo.bin` together as assets of that hardware release.

The J71b device descriptor selects
`https://github.com/OrchardLinux/hardware/releases/latest/download/j71b-launcher.boot.toml`.
The selected manifest references immutable assets; the CLI verifies their sizes
and hashes. No distro registration or prior host installation history is used.
Until those release assets are published, the default command cannot download
them; use `orchard launch --manifest PATH/boot.toml` for local development.

A build or unit test is not physical-device or visual qualification. Verify
black-screen presentation and cold-restart launch on supported hardware before
announcing the release.

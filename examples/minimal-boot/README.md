# minimal-boot

A kernel, BusyBox and a USB serial root shell, running entirely from RAM.
The content is `init`: read or edit it to change what runs on the iPad.
No build/run wrapper scripts or example registration.

```sh
cd examples/minimal-boot
orchard build --device j71b
orchard run
```

`orchard` runs the checkout it is started in (see the [README](../../README.md#develop)).
The board is yours to choose: `--device`, or `device` in `orchard.local.toml`
(this checkout) or `~/.config/orchard/config.toml` (all of yours).

## What gets built

- `init` becomes `/init`, the device's startup script.
- BusyBox provides the shell and basic utilities.
- Optional `rootfs/` files are copied to matching paths in the RAM filesystem.

To add a program, put an executable at `rootfs/usr/bin/my-tool`. To change startup,
edit `init`. There is no Orchard device agent, RPC protocol, installer, network
service or storage mount. Kernel modules are omitted unless you pass `--modules`;
that stages matching modules but does not activate them.

The CLI handles compilation, caching, packing and uploading directly.

## Host requirements

Install uv, Git and a running native ARM64 Podman environment. Local Pongo builds need
macOS/Xcode/iPhoneOS SDK; Linux builds can reuse a local binary with `--pongo FILE`.
Booting needs palera1n and libusb.

The reference kernel/DTB targets **J71b / iPad 6 Wi-Fi**. Other devices can be
tried after a mismatch warning; this is not a claim that the reference payload
will boot them. The original charger payload booted on J71b; this extracted
runtime is only offline-tested so far.

## Serial access

This example's [`payload.toml`](payload.toml) sets `transport = false`: serial only,
no USB network or SSH (`orchard shell`). `orchard push/pull --serial` still work.

After `Boot this device? [Y/n]`, connect to the actual new USB serial port:

```sh
screen /dev/cu.usbmodemYOUR_PORT 115200   # macOS
screen /dev/ttyACM0 115200               # Linux
```

This is **unauthenticated root access over physical USB**. Use a trusted host and
dedicated device. Supplied startup does not install, initialize storage, load
modules or run experiments automatically. Kernel initialization can still affect
hardware. Upload success is not proof of Linux readiness.

## Outputs and reuse

- `~/.cache/orchard/`: shared upstream Git objects, immutable built components, the
  kernel tree per platform (`kernel-work/`) and the compiler cache, for every checkout.
- This directory's `out/`: its own boot payload and local receipt.

`XDG_CACHE_HOME` is respected; `ORCHARD_CACHE_DIR` or `--cache DIR` overrides the
cache root. Identical inputs in another checkout reuse the built kernel without
downloading, checking out or compiling Linux again. Kernel changes are patches in
this checkout's `kernel/`; the shared tree applies only their difference.

Changing `init` or `rootfs/` only repacks the payload. External-driver and final-DTB
edits reuse the kernel base and its matching module SDK; they do not compile the
upstream kernel. In-tree source/config/patch changes select a new kernel base.
Do not delete the shared Git object store while editable sources borrow from it.

See [charger-001](../charger-001/) for an example with actual tools in `rootfs/`,
and [orchard's build functions](../../docs/ram-development.md) for lower-level use.

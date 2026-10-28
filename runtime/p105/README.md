# P105 installed-system hardware runtime

This runtime targets the original iPad mini Wi-Fi, `iPad2,5` / `p105ap`,
with its A5 ARMv7 processor. It enters an ARMv7 systemd NFS root through a
separately built Cascadia RAM image. Device validation used Arch Linux ARM
`armv7h`. The [recovery sources](recovery/) add a limited Orchard device RPC
for status, diagnostics, serial SSH bootstrap and an authenticated NFS handoff.
Desktop assembly belongs to the image builder.

The runtime preserves Cascadia's touchscreen calibration sequence, PMU button
handling and wireless power-cycle timing. Kernel drivers own SGX startup,
Bluetooth patchram, charging and speaker clocks. Public resources here contain
shell scripts, service units, configuration and patches. The installer takes
compiled tools and private firmware from an external verified initramfs.

## Prepare external inputs

Use the pinned Cascadia source preparation in [kernel/p105](../../kernel/p105/README.md).
It applies these runtime patches in order against
`c20b85ebb951c704d7e3a664c378dde8276e3928`:

1. `patches/0001-p105-systemd-handoff.patch` adds the installed-root marker branch.
2. `patches/0002-p105-desktop-home.patch` adds opt-in generic Home forwarding.

Extract calibration from the target iPad and retain it as a private file.
Supply it to Cascadia as `build/keep/lib/firmware/mtcal.bin` before building.
The selected initramfs must contain that same calibration. The installer
requires a separate, regular `--calibration` file outside the selected
initramfs tree. It rejects a symlink, compares the bytes to the boot tree
before installation and copies the explicit file. Obtain firmware through Cascadia's
extraction flow from the user's own IPSW.

The source tree contains no Apple firmware, panel calibration, SSH keys or
compiled hardware tools. The exact external payload paths are listed in
`overlay/usr/lib/cascadia/p105-payload.list`. Static ARM tools are `z2-boot`,
`p105-keys` and `busybox.static`. The key tool must include `--desktop-home`.

## Install into a guest root

Python 3.9 or later is required. Run this from the hardware source tree or its
installed package directory:

```sh
python3 runtime/p105/install.py \
  --root /work/p105-root \
  --initramfs /work/cascadia/build/initramfs-root \
  --calibration /work/private/own-panel-mtcal.bin
```

The installer requires root to preserve guest ownership. For a staging tree,
use `--allow-user-owned-staging` and apply numeric guest ownership before
exporting it. It rejects the host root, an incompatible root marker, payload
paths escaping the selected tree, non-ARM tools and a calibration mismatch.

The guest must supply ARM32 systemd, `/usr/bin/sh`, coreutils, grep and udev.
A desktop environment is supplied separately when needed.
Configure its account, SSH authentication, DNS, NFS export and desktop session
in the system assembler. Enable NetworkManager for wireless and keep the
existing USB link configured during the pivot. The runtime's NetworkManager
configuration leaves the pinned USB gadget MAC unmanaged; its link file keeps
`usb0`. Remove inherited network-manager services that compete for that link.

Installed helper paths remain `/usr/libexec/p105`. The installer enables
`p105-hardware.target` and the physical USB rescue console. It records local
payload hashes in `/usr/lib/cascadia/p105-payload.sha256` and installation
metadata in `p105-runtime.json` beside it. Those generated records stay with
the private guest root.

## USB and NFS prerequisites

Native Orchard recovery accepts its NFS location through the authenticated
device protocol. Its producer replaces the manual init and removes any
inherited `etc/nfsroot`. Follow the companion CLI's `docs/p105.md` for that
flow. The root selection below applies to the manual Cascadia boot.

The tested gadget uses `10.55.0.2/24`; configure its host interface as
`10.55.0.1/24`. Serve the assembled guest root over NFSv3/TCP to that client,
with numeric guest ownership and executable modes preserved. The host must
keep serving that filesystem throughout the Linux session.

Before building the private Cascadia image, set its root selection file:

```sh
install -d build/keep/etc
printf '%s\n' '10.55.0.1:/absolute/path/to/exported-root' > build/keep/etc/nfsroot
```

The actual export location belongs to the owner's host. Configure the host's
NFS service and USB address before booting. On macOS this needs an interactive
administrator Terminal session and approval for system-configuration access
when requested. NFS and account provisioning belong to the host/image builder.

## Boot contract

The Orchard RAM init waits for one identity-bound handoff request. It prepares
the installed root, moves the early mounts and retires its first serial shell
before entering systemd. Diagnostics keep their pinned SSH key through a RAM
listener on port 2222. The installed serial rescue service then owns `ttyGS0`.

`/etc/cascadia/systemd-root` contains `p105-systemd-v1`. Cascadia's RAM shell
calls the installed `prepare-root` before moving mounts. It refreshes the
static tools and firmware from the boot image, creates `/run` and `/tmp` as
RAM filesystems and seeds a static rescue shell under `/run/p105-rescue`.
The first stage moves `/dev`, `/proc` and `/sys` with their mounted children,
then enters `/usr/libexec/p105/systemd-init` as PID 1.

The marker branch skips Alpine stage2 updates. A failed preparation unmounts
its temporary filesystems and returns to Cascadia's RAM stage2. Early USB
reset loops and the timer-sensitive setup remain in the pinned first stage.
Linux-owned internal NAND installation is outside this adapter's contract.

The ACM serial port exposes an unauthenticated root rescue shell over physical
USB, matching Cascadia's bench recovery behavior. Its shell, applets, working
directory and history live in RAM so the console survives an NFS stall. Keep
that port free of another getty; if using `console=ttyGS0`, mask
`serial-getty@ttyGS0.service` in the guest.

A desktop session unit should require the hardware target:

```ini
[Unit]
Requires=p105-hardware.target
After=p105-hardware.target
```

The target requires the platform check, persistent `z2-boot` process and key
daemon. Touch calibration continues after the process starts, as in Cascadia's
background launch. Keep `z2-boot` running because it retains the touchscreen
file descriptor after handing reports to the kernel.

## Hardware behavior

`p105-wifi-chip.service` waits for a cfg80211 interface. If absent, it waits
15 seconds before cycling the HSIC EHCI host at `36400000.usb`, keeps power
off for one second and retries at most twice. This uses the same bounded
recovery as Cascadia's `wifi chip`. NetworkManager starts afterward and owns
association, addresses and DNS. The USB gadget carrying NFS uses a separate
controller. A radio failure appears in the journal while the desktop can start.

The target starts native `bluetooth.service`; `hci_bcm` handles the UART,
GPIOs, baud rate and HCD firmware in the kernel. The copied firmware remains
available for controller resets. Cascadia's `asound.conf` provides ALSA's
mixed 48 kHz S16_LE stereo default on card `iPadMini`. The MCA and amplifier
drivers preserve their own power sequence. PipeWire and WirePlumber use their
normal ALSA discovery. Keep SGX autoboot enabled for native DRM/KMS and retain
`apple_pmu_charger.armed=1` in the board's boot arguments.

`p105-keys.service` opts in with `--desktop-home`. A short screen-on Home release
emits `KEY_HOMEPAGE` down/SYN/up/SYN through the distinct `Cascadia P105 Home`
uinput device. Its udev rule marks it as keyboard input; the compositor binds
the generic key to its action. Exact `gpio-keys` name matching prevents the
daemon from grabbing its virtual device. Cascadia's no-argument RAM launch
retains its existing behavior. The 800 ms hold still controls rotation lock,
with unchanged PMU feedback. Hold, volume and side-switch behavior stay in the
hardware daemon.

Rebuild the Cascadia bundle whenever its static tools change. Every NFS boot
copies `/sbin/p105-keys` from RAM and refreshes the checksum, so the boot image
and installed unit must support the same option.

## Verification

```sh
systemctl --no-pager --failed
systemctl status p105-platform.service p105-touch.service p105-keys.service
journalctl -b -u p105-touch.service -u p105-wifi-chip.service
(cd / && sha256sum -c /usr/lib/cascadia/p105-payload.sha256)
```

The touch journal should reach `READY` with a successful ioctl. Check that PID 1
is systemd, `/` uses the selected NFS export and both `/run` and `/tmp` use tmpfs.
Read `/proc/bus/input/devices` to confirm the physical and virtual keys. Test
Home, screen power, volume and wireless through the physical device, then repeat
boot before making device-support claims. Native package and syntax checks
verify the artifacts; hardware tests establish their behavior.

Cascadia source and its hardware investigations:
[Project-Cascadia](https://github.com/rzxvx/Project-Cascadia/tree/c20b85ebb951c704d7e3a664c378dde8276e3928).
The runtime scripts and patches use `GPL-2.0-only`, as marked in each source.

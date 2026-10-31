# J81 follow-up: experimental native-root qualification

This records the separate contributor experiment; it does not qualify the
unchanged Orchard main kernel or enable its installer. Experimental overlay
modules, device-tree additions and host tools remain in the [pinned contributor
source](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/experimental/j81-storage/README.md), as CONTRIBUTING.md requests.
J82 was not tested. The receipts identify the exact hardware-tested artifacts;
this PR only changes documentation.

The full original Orchard Omarchy A v0.1.1 archive was installed to
`/dev/asp0n1p2` and reached a real native desktop. Global PID 1 is `systemd`;
`/` is writable ext4 on internal p2, with no NFS or RAM overlay root. The
persistent boot counter recorded 1. A Wayland screenshot was captured, and the
owner confirmed the desktop on the physical iPad. Labwc and the Omarchy shell
ran as UID 1000, with zero failed system units. Owner Wi-Fi autoconnect, DNS and
an HTTPS request returning 200 passed.

## Layout and installation evidence

Apple's own framework successfully resized the existing APFS area to 16 GiB.
The guarded GPT tool updated and verified both copies, followed by a successful
normal iPadOS reboot. Apple partition identity/start/name/flags were retained.
Linux p2 occupies logical LBA 4,194,816..15,624,703, with 4096-byte logical
blocks: 46,816,821,248 bytes. All nine auxiliary namespaces remain read-only.

The exact known-working kernel, DTB, loader and boot arguments were retained.
The matching ASP module is SHA256
`3e36f4b045a38ba4eeb46cce3a24a9d19955568b5831cd37e4124b5d6d6961f9`.
Its immutable write window covers only p2. These are software guards above a
global controller WRITE_UNLOCK, not a separate hardware lock for each namespace.

Native e2fsprogs formatted p2, and e2fsck passed. The original rootfs archive
SHA256 was verified on the iPad before extraction:
`a5850a1575f52edab451783c3e4fdc2ac8cf727d5740c135c3929ff732da255c`.
Numeric ownership/modes and archive attributes were retained. The J81 overlay
installed matching modules/owner-provided firmware, private Wi-Fi and SSH state,
a native fstab/identity guard, USB networking and the boot proof/counter.
J71b-specific units were masked in the installed copy. The original Mac rootfs
was not modified. Arch unit parsing and handoff preflight passed before startup.

## Fixes that made the first native boot work

1. BusyBox standalone shell applet selection bypassed PATH for names it knew.
   A bare `mke2fs` selected BusyBox and rejected `-t` before formatting.
   Explicit `/usr/bin/mke2fs`, `/usr/bin/e2fsck` and `/usr/bin/blkid` select the
   pinned native tools. A private RAM-file preflight verified those tools before
   the controlled installation continued.
2. The handoff loop sent SIGTERM to its interactive `sh -i`, then waited.
   BusyBox ash ignored that signal, leaving PID 1 in its wait. After checking
   the exact child's command, parent PID and ignored-signal mask, SIGKILL of
   that child released the already-requested handoff without reboot. The source
   loop now uses SIGKILL only on its recorded disposable shell child.
3. The handoff preserves `/proc`, `/sys`, `/dev` and `/run`, then executes
   `switch_root` as global PID 1. A bind of the existing device tree supplies
   `/dev/null` for the preliminary native version check; temporary binds are
   removed before handoff. A node created under the nodev `/run` mount does not
   provide working device access.

## Fresh boot: persistence verified

The owner marker was fsynced to internal storage, and systemd rebooted cleanly.
The automatic native bootstrap then stopped during the early BCM4350 Wi-Fi
probe, before USB enumeration and before the native-root mount stage. The
owner supplied the photographed stopping point. No reinstall was attempted.

The revised bootstrap defers this exact Wi-Fi PCI modalias to installed
systemd/udev so that recovery USB and the local root can start first. It keeps
the qualified kernel/DTB/loader/bootargs and p2 fence. The revised image
`image-native-bootstrap/out-9f83bcb8356f` passed a fresh boot: a different kernel
boot ID, counter 2, the exact surviving fsynced owner marker, unchanged SSH host
key and a returned desktop screenshot. Wi-Fi autoconnect, DNS and HTTPS 200
passed again; zero failed system units and all nine auxiliary read-only flags
were confirmed. The p2-only write fence was unchanged. No reinstall occurred.
[Fresh-boot result](native-reboot-result.json).

## Scope for the maintainer

The initial requested refactor validation and packaging fix are recorded in
[the integration report](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/integration/2026-10-29/REPORT.md). Both Pongo
source variants failed in the [paired comparison](pongo/COMPARISON.md);
quiet mode alone did not fix source-built Pongo. The pinned known-working
prebuilt remains the qualified control.

The native installation and fresh-boot persistence are demonstrated, but this
is still an experimental, unit-specific port. Only gpio-keys was detected as
input in the native session;
native touch, physical audio, Bluetooth, accelerated graphics, suspend, charging
policy and long-term reliability remain work. Cold boot still needs a host via
Pongo/m1n1 and retains Apple boot support. No untethered power-on boot is claimed.

See the [sanitized measured result](native-install-result.json)
and [source map](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/experimental/j81-storage/README.md). Raw GPT/APFS data,
UUIDs, ECID, credentials, keys, owner firmware and built private images remain
local. The 48 host guard/parser tests passed; those simulations supplement the
explicit hardware results and do not qualify arbitrary devices or installs.

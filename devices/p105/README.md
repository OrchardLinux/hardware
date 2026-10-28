# iPad mini 1 Wi-Fi (P105)

Experimental hardware prerequisites and component builds for the original
iPad mini Wi-Fi. The public resources describe the board and its firmware
inputs; the boot components use Cascadia and vetted Legacy iOS Kit tools.
The companion Orchard CLI contribution supplies a native P105 adapter for
private tethered recovery and USB/NFS handoff. Public launch assets await
release integration.

| Identity | Value |
| --- | --- |
| Apple product | iPad2,5 |
| Board / device tree | P105AP / apple,p105ap |
| SoC / DFU chip | Apple A5 S5L8942X / 0x8942 |
| DFU board | 0x0a |
| Userspace ABI | 32-bit ARMv7, hard float |
| Physical RAM | 512 MiB |
| Panel | 768 x 1024 |

The native recovery gadget uses `Orchard-P105`, with a boot-bound USB serial
number. macOS reports its product as `Orchard_P105`. The device descriptor
retains the generic CDC gadget metadata used by the earlier manual boot;
native discovery validates the recovery identity through the CLI adapter.
The capability flags describe observed hardware behavior.

## Manual boot prerequisites

- An iPad2,5 with a working jailbreak. The qualified device ran iOS 9.3.5
  (13G36), with an untethered jailbreak and OpenSSH installed through Cydia.
- A Lightning USB connection, a trusted computer and access to the owner's
  iOS SSH credentials. The jailbreak supplies the kDFU entry path.
- A macOS or Linux host with the dependencies reported by Cascadia's doctor,
  a working Docker-compatible build environment, and Legacy iOS Kit's host
  USB tools. The local qualification used a Mac.
- The Apple iOS 8.4.1 / 12H321 IPSW pinned in firmware.json and the owner's
  own multitouch calibration. The running iOS version and firmware-source
  archive serve different steps of the boot path.
- A 32-bit ARMv7 root filesystem and a compatible kernel/runtime. The
  qualified desktop used an external NFS root through USB; installed-root
  policy belongs to its image builder.

The component producer and source preparation entry points are documented in
[the P105 kernel recipe](../../kernel/p105/README.md):

~~~sh
python3 kernel/p105/prepare-source --output ../cascadia-p105
cd ../cascadia-p105
./cascadia doctor
~~~

These commands prepare source outside the hardware checkout and check the
build host. Follow the kernel recipe for its pinned Linux commit checkout,
private firmware inputs and RAM bundle build. The source preparer applies
the P105 kernel fragment and runtime patches from this repository.
Cascadia's `flash --kdfu` selects the paired plaintext iBEC generated from
the pinned IPSW. See [the runtime handoff](../../runtime/p105/README.md) when
preparing an installed system; its installer requires an explicit owner
calibration file alongside the verified initramfs tree.

The earlier physical qualification used upstream revisions
[Cascadia c20b85e](https://github.com/rzxvx/Project-Cascadia/tree/c20b85ebb951c704d7e3a664c378dde8276e3928)
and [Legacy iOS Kit 23622f8](https://github.com/LukeZGD/Legacy-iOS-Kit/tree/23622f8fe72c09952089d5145a7b008cfcc90d9b).
Cascadia's [quick start](https://github.com/rzxvx/Project-Cascadia/blob/c20b85ebb951c704d7e3a664c378dde8276e3928/docs/QUICKSTART.md)
describes the host setup and jailbreak-assisted USB boot sequence.

## Firmware and private calibration

firmware.json retains the shared device, IPSW, extracted-input and destination
fields used by the existing J71b recipe. Its preparation mode is explicitly
manual and source-only. The encrypted root filesystem needs Cascadia's pinned
extractor, and generated entries name Cascadia source steps. The CLI verifies
the owner's prepared firmware against this recipe. Extraction remains an
explicit owner preparation step.

P105.mtprops is generic Apple touchscreen firmware extracted from the IPSW.
The separate mtcal.bin comes from the owner's panel calibration in syscfg
MtCl. The pinned upstream source contains another device's reference
calibration; source preparation excludes it and the runtime requires the
owner's file. Keep calibration bytes and their hashes out of commits,
public manifests and release artifacts.

The generic output hashes cover the pinned archive and source generators.
The Wi-Fi image hash uses Cascadia's public synthetic MAC default. A custom
MAC produces a different image and needs a host-local receipt. The recipe
also records the Broadcom Bluetooth patchram and SGX microkernel required
by the qualified runtime. The BCM4334 build rejects the CLM-loading path,
so the recorded recipe supplies its combined TRX/NVRAM image.

IPSW downloads, decrypted firmware, patched Apple bootloaders and device
calibration stay on the owner's host. This directory contains metadata and
instructions.

## Current component qualification

The packaged producer's private bundle booted this device on 27 October 2026
and reached the Arch / Omarchy A desktop through the preserved USB/NFS root.
System and user managers reported zero failed units. The hardware payload
checksums passed, while the flash controller stayed disabled with an empty
MTD inventory. The owner calculated 2 + 2 = 4 in Omacalc and opened the app
grid with Home.

Touch responds slowly and sometimes needs repeated taps, matching the owner's
experience with the earlier desktop. This remains an open issue. See the
[component validation record](../../docs/p105-validation.md) for the tested
scope and the package checks.

The companion CLI subsequently passed repeated native recovery boots and NFS
handoff, preserving its pinned diagnostics and the RAM rescue console. See
[the Orchard validation record](../../docs/p105-orchard-validation.md).

## Earlier physical qualification

The results below describe the original manual build and its desktop root.

On an original iPad2,5 with an A5 and 512 MiB RAM, a native Arch Linux ARM
armv7h desktop ran on Cascadia Linux 6.12 with a USB/NFS root. A repeat boot
reached graphical.target in 27.478 seconds. Both system and user managers
reported zero failed units with the document portal running after FUSE was
built in. Display qualification used the native panel with Pixman and Qt
software rendering.

The user confirmed touch input by calculating 2 + 2 = 4 in Omacalc. Foot and
Firefox launched on the iPad. Physical Home opened the application grid with
the runtime's opt-in --desktop-home forwarder. Wi-Fi association succeeded
through the on-iPad password UI. USB networking carried NFS and SSH, and the USB serial rescue
console remained available.

The desktop runtime used systemd namespaces and cgroups, built-in FUSE,
uinput and a 256 MiB LZ4 zram device. Those requirements are recorded in the
kernel and runtime resources linked above. A user-supplied root must provide
its own session, service and package policy.

Bluetooth pairing and audio playback remain untested. Accelerated graphics
still need qualification. Linux reboot currently powers the device off;
starting Linux again requires the host boot flow. Tethered boot and the
external root remain part of this qualified setup. P105 registration and
manual component preparation cover this contribution; release integration
remains part of later release work. The companion CLI adapter handles the
private tethered recovery and NFS workflow.

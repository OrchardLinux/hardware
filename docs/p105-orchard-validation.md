# P105 Orchard recovery validation

On 27 October 2026, Orchard CLI 0.1.11 booted the original iPad mini Wi-Fi
through its qualified iOS 9.3.5 jailbreak into RAM recovery. The authenticated
NFS handoff reached the preserved Arch Linux ARM / Omarchy A desktop. A second
cold boot qualified the final console cleanup in the recovery init.

The local contributions pair hardware 0.1.11 with CLI 0.1.11 and the ARMv7
recovery composer in Minimal Linux. Each repository has a separate branch.
The desktop filesystem still comes from the preserved development root; its
migration into the current Omarchy A builder belongs to phase 4.

## Physical checks

| Check | Result |
| --- | --- |
| Boot transport | UDID-bound iOS SSH, verified kloader and iBEC, then one bundle upload and one loader upload |
| Cable wait continuation | The repeat test resumed its saved iBEC wait with the same target and verified assets |
| Recovery identity | Provisioned owner ECID and live kernel boot UUID checked through USB, serial bootstrap and pinned SSH |
| NFS handoff | One authenticated request; the same device, boot UUID and SSH host key remained valid after the pivot |
| Installed system | NFS root ready, graphical target active, Labwc and desktop shell running |
| Service health after touch recovery | Zero failed system units; platform, touch and Home services active |
| Hardware payload | All eight installed checksums passed |
| Rescue console | Exactly one reader of `ttyGS0`, rooted in the installed system; physical serial commands ran the rescue shell from RAM |
| Flash fence | Controller disabled and MTD inventory empty |
| Owner interaction | Desktop visible; physical Home opened the app grid on the first native boot |

The repeat boot's touchscreen daemon exhausted its calibration attempts and
exited. A single service restart restored it in the same Linux session. The
driver reported missing attention signals during that failed startup. This
intermittent calibration failure remains open alongside the slow touch and
repeated taps observed in the earlier desktop. The owner accepted slow touch
for the tethered milestone. Logs retain the failed startup and recovery.

The final repeat used installed wheels while Python audit hooks denied reads
from all five Orchard checkouts and the original source workspace. The host's
NFS service continued serving the existing desktop dataset. These hooks
exercise package independence within the Python processes; the kernel and
NFS service have their own access to the exported root.

## Host and package checks

The CLI suite passes 161 tests on Python 3.9.6 and 3.14.6. Its adapter type
checks pass for both profiles. Hardware passes 107 tests on both versions;
Minimal Linux passes 47 tests. Fixtures cover ambiguous USB discovery and
unrelated Apple devices, alongside identity checks during re-enumeration.

The hardware wheel and source archive carry all 41 P105 resources with bytes
matching the checkout. CLI archives include every P105 module. Package audits
check for owner calibration, private access material and generated boot files.
The Minimal Linux base uses signed Alpine ARMv7 packages. Public userspace
checks ran under QEMU's Cortex-A9 model before physical qualification.

The recovery kernel retains P105's namespace, FUSE and zram settings. Its USB
fragment selects configfs ACM/ECM and disables `CONFIG_USB_CDC_COMPOSITE`.
Source tests cover the managed configuration block and refuse output paths
inside the owner's input tree.

macOS supplied `Orchard_P105` as the product name and masked the link MAC in
`ifconfig`. The adapter accepts the measured product spelling and obtains the
network interface and MAC from children of the selected USB device. Existing
host addresses remain owned by their original setup.

## Remaining work

The host supplies boot components and serves the root throughout the session.
The owner manages the existing NFS export. Orchard records any address it adds
to the selected USB link and provides a cleanup command after shutdown.

Internal installation requires the later A5 storage work. Bluetooth pairing,
audio playback and accelerated graphics still need physical tests. Public
release assembly follows the migrated ARMv7 desktop builder. Private firmware,
calibration and patched Apple boot components stay on the owner's host.

Private receipts and device logs are retained under the owner's local P105
qualification directory. The earlier manual component test remains recorded
in [p105-validation.md](p105-validation.md).

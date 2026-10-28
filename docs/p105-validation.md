# P105 component validation

This record covers phase 2. The later native CLI boot and recovery checks are
recorded in [p105-orchard-validation.md](p105-orchard-validation.md).

The P105 contribution builds ARMv7 kernel components from pinned Cascadia and
Linux source. An explicit private initramfs selects a boot bundle containing
the owner's firmware. The public producer embeds an empty initramfs.

## Host checks

The phase 2 hardware suite passed 98 tests on Python 3.9.6 and Python 3.14.6.
These include source export, matching owner calibration and ARM ABI checks.
The component tests reject a public output containing a private payload and
verify the exact layout of a private bundle.

Private staging creates the empty early mount directories before archiving.
Directory entries and modes are included in the inventory, with regression
coverage for empty mount points and linked destinations.

The optional NFS location is accepted only for private roots. Tests reject
relative locations and embedded line breaks before any build work starts.

The phase 2 wheel and source archive contained all 38 P105 resources with bytes matching
the checkout. A fresh environment installs the wheel without a sibling source
dependency. The installed runtime stages its payload into a disposable ARMv7
guest root using real ARM executables and the new build's static tools.

Private compilation from the installed wheel ran under Python audit hooks
that denied reads from both source checkouts. A separate probe confirmed the
hooks rejected reads from each tree. Compiler containers mounted only the
exported source and explicit build inputs, with networking disabled.

The compiler image was built from the packaged Containerfile. Component
receipts record the image identity and tool versions. Keep private receipts
on the owner's host because they include hashes of private inputs.

## Device qualification

On 27 October 2026, the installed hardware package produced a private bundle
that booted an iPad2,5 from iOS 9.3.5 build 13G36 through its existing jailbreak
and the manual iBEC transport. The bundle handed off to the preserved Arch
Linux ARM / Omarchy A root through USB NFS.

| Check | Observed result |
| --- | --- |
| Kernel and board | Linux 6.12.0, `apple,p105ap` |
| Root filesystem | NFS over USB |
| Desktop | `graphical.target` active; Labwc and the desktop shell running |
| Service health | Zero failed system units and zero failed user units |
| Hardware activation | Platform, touch and Home-forwarding services active |
| Installed payload | All eight hardware payload checksums passed |
| Physical input | Omacalc returned 4 for 2 + 2; Home opened the app grid |
| Internal flash | Controller disabled, empty MTD inventory, no bound flash device |

Touch responds slowly and sometimes needs repeated taps. The owner observed
the same behavior in the earlier desktop and accepted it for this milestone.
Touch latency remains an open issue.

The ordinary P105 configuration disables `CONFIG_MTD_APPLE_H2FMI` and marks
the flash-controller node disabled. The remainder of the reference MTD
configuration is retained to keep the change scoped to this device's driver.
The on-device inventory confirmed that the controller stayed unbound.

The final source's private staging matched the tested bundle's input tree,
including empty directories and modes. Private receipts retain the component
hashes and the device checks on the owner's host.

The earlier Wi-Fi, application and display measurements in the device profile
remain attributed to the original manual build. This test qualifies the new
hardware components with the preserved desktop root. The companion CLI phase
has since passed the native qualification linked above. A desktop built
entirely from the new checkouts awaits its builder phase.
Bluetooth pairing, audio playback and accelerated graphics need device tests.
The current boot still needs the host transport and its NFS export.

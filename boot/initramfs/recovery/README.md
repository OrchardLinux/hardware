# Orchard recovery

One RAM recovery image for every device that shares the kernel: it carries each device's DTB and m1n1 boots with the matching one.

Public recovery includes all modules, indexes, device scripts, a Python runtime from the verified public Arch base, and static BusyBox, Dropbear and Wi-Fi tools (`wpa_supplicant`, `wpa_cli` and `iw`; WPA2, libnl, internal crypto). RAM images get the same tools. Private firmware is transferred only after fresh ADT retrieval.

- `python -m orchard.image.prepare_runtime`: stage public files using explicit base, module and root-receipt inputs.
- `python -m orchard.image.recovery`: pack the runtime and assemble loader, bootargs, the DTBs, kernel and initramfs.
- `init`: early pseudo-filesystems, USB key provisioning, and the switch to the installed system after `orchard-device start`.
- Device operations (`bootstrap`, `status`) and the storage fence: hardware `runtime/recovery/`; shared payload generation: `orchard.image.runtime`.
- Distro assembly is owned by its consumer, not this package; see [BUILD.md](../../../../BUILD.md).

The default keeps framebuffer console output hidden with `fbcon=map:9`, while retaining normal UART logging for hardware timing compatibility. `--verbose` explicitly enables visible diagnostics. Logo preservation remains mandatory; a later modeset still needs hardware verification.

Initial SSH access is key-only, provisioned through the physical USB serial shell.

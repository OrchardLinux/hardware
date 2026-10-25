# J71b recovery

Public recovery includes all modules, indexes, device scripts, a Python runtime from the verified public Arch base, and static BusyBox/Dropbear tools. Private firmware is transferred only after fresh ADT retrieval.

- `python -m orchard_hardware.image.prepare_runtime`: stage public files using explicit base/module/root-receipt and image policy inputs.
- `python -m orchard_hardware.image.recovery`: pack the runtime and assemble loader, bootargs, DTB, kernel and initramfs.
- `init`: early pseudo-filesystems, USB key provisioning and PID 1 handoff coordination.
- Device operations: hardware `runtime/j71b/`; shared payload generation: `orchard_hardware.image.runtime`.
- Distro assembly is owned by its consumer, not this package; see [BUILD.md](../../../../BUILD.md).

The default keeps framebuffer console output hidden with `fbcon=map:9`, while retaining normal UART logging for hardware timing compatibility. `--verbose` explicitly enables visible diagnostics. Logo preservation remains mandatory; a later modeset still needs hardware verification.

Initial SSH access is key-only, provisioned through the physical USB serial shell. On handoff the current authorized key and host key remain in RAM; the installed diagnostic service reuses them on the USB address, independently of desktop startup.

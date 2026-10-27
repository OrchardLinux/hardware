# J71t recovery

J71t (iPad 5 Wi-Fi, S8003) uses the same RAM recovery as [J71b](../../j71b/recovery/README.md). Only the files here are device-specific:

- `init`: checks for `apple,j71t` and starts `/usr/libexec/orchard-j71t/handoff.py`.
- `usb-gadget`: presents `J71t Recovery`, which the CLI adapter matches in Linux mode.
- `boot.json`, `boot-verbose.json`: boot arguments, including the NVMe write policy below.

BusyBox, Dropbear, `authorize-key`, `start-ssh`, `capture-adt` and `udhcpd.conf` come from the J71b recovery directory unchanged. The device API in `runtime/j71b/` is shared; J71t adds `runtime/j71t/phase2-hardware` and `j71t-check-platform`.

## Storage policy

The J71t kernel keeps NVMe read-only unless `nvme.write_window=NSID:FIRST-LAST` is set at boot. Only NVMe Write commands inside that LBA range reach the controller; trim, Write Zeroes, Format, Sanitize, firmware and namespace commands stay blocked, and namespaces 2-7 (Apple system namespaces) stay read-only whatever the window says.

Orchard selects the target partition after boot, so recovery opens the whole of namespace 1 (`1:0-4294967295`) and relies on the same block-layer fence as J71b: `phase2-hardware` sets every `nvme0n1p*` read-only, and the device API makes only the confirmed target writable while it formats or mounts it. The GPT and the iPadOS partition are therefore protected by the block layer, as on J71b, not by the kernel's command filter.

The runtime accepts any window confined to namespace 1. Someone who knows their Linux partition's LBA range can build recovery with that narrower window in `boot.json` and keep the kernel-level fence; writes to a target outside it then fail with I/O errors and nothing outside the window is written.

The kernel also holds the PCIe host and the ANS controller (`CONFIG_NVME_APPLE_ANS_EARLY_USB`) until `phase2-hardware` releases them, after the private firmware mixin has been written.

# Boot contract

What an installed system has to put on a device so Orchard can boot it. It is
two standards, applied with as few additions as possible:

- [Boot Loader Specification](https://uapi-group.org/specifications/specs/boot_loader_specification/) (BLS), Type #1 entries;
- [Discoverable Partitions Specification](https://uapi-group.org/specifications/specs/discoverable_partitions_specification/) (DPS), for partition types.

Status (2026-10-29): agreed direction, not yet implemented by the CLI or an
installer. It replaces the old layout (root directory on an "OmarchySystems"
ext4 partition, launcher kernel, `switch_root`). Old installs are not migrated.

## Who does what

| Part | Does | Never does |
|---|---|---|
| Bootloader (m1n1 + host) | Reads the boot partition, lists the entries, loads the chosen entry's kernel, initrd and DTB, applies the ADT fill-ins, boots | Mount or read a root filesystem, write to storage |
| Installer | Creates the boot partition once; creates a root partition per system; runs the system's own install | Touch APFS beyond the agreed resize; touch another system's partition or files |
| Installed system | Owns its root partition and its own files and entry on the boot partition; updates them with its normal kernel-install hooks | Touch another system's files |

## Partitions (GPT)

| Partition | Type GUID | Filesystem | Count |
|---|---|---|---|
| Apple container | (as Apple left it) | APFS | 1, untouched |
| Boot | XBOOTLDR `bc13c2ff-59e6-4262-a352-b275fd6f7172` | FAT32 | Exactly 1, shared by all systems, at least 1 GiB |
| Root | DPS arm64 root `b921b045-1df0-41c3-af44-4c6f280d3fae` | The system's choice | 1 per system |

- **XBOOTLDR, not ESP.** There is no EFI firmware. XBOOTLDR is the BLS/DPS type for a boot partition that a non-EFI loader reads. More than one XBOOTLDR partition is an error, never a choice.
- **Partition identity only.** Roots are named by PARTUUID in their entries, never by partition number or position.
- **Inside its root partition, a system uses its native layout,** for example upstream Omarchy's btrfs subvolumes.

## Entries

The layout follows BLS:
- one file per entry under `/loader/entries/<entry-id>.conf` on the boot partition;
- the entry's files under `/<entry-token>/<version>/`.

The entry token is the system's BLS entry token, for example its machine-id or `IMAGE_ID`. It keeps systems apart.

```
title      Omarchy
version    6.18.2-orchard
sort-key   omarchy
linux      /6a1f…/6.18.2-orchard/linux
initrd     /6a1f…/6.18.2-orchard/initrd
devicetree /6a1f…/6.18.2-orchard/t8010-j71b.dtb
options    root=PARTUUID=4d6c…-… rw quiet
```

| Key | Orchard rule |
|---|---|
| `linux` | Required. arm64 `Image`, raw or gzip-compressed |
| `initrd` | Optional and repeatable. Several are concatenated in order, as BLS specifies |
| `devicetree` | **Required.** The DTB for this board. m1n1 applies the ADT fill-ins (MAC addresses, calibration, memory, CPUs) to it, so the system ships the plain DTB from its kernel build. systemd's `kernel-install` copies it when `/etc/kernel/devicetree` names it. |
| `options` | Required. It must contain `root=PARTUUID=…` of the system's own root partition. Root discovery without `root=` is not used, because one device can hold several roots. |
| `title`, `version`, `sort-key` | As BLS: shown in the host's list and used for its order. |
| `machine-id`, `architecture` | Optional. If `architecture` is present, it must be `aa64`. |
| `devicetree-overlay`, `efi`, `uki` | Not supported. Such entries are listed as unbootable. |

**Boot counting** (`+tries` in the file name) is not supported yet. Such entries boot as if they had no counter.

## Selection

The host lists every valid entry in BLS order and boots the one the user picks. Invalid entries are listed with the reason and never hidden. Reasons include:
- missing files;
- a `devicetree` whose name doesn't match the board;
- a missing `root=PARTUUID`.

Without a choice, the host boots the first entry in BLS order. A remembered default, like systemd-boot's `loader.conf`, can come later.

## Reading it

The host reads the boot partition through m1n1's generic `main_storage_*` proxy calls and parses GPT, FAT32 and the entries itself, so the host needs no device-specific code. Which SoCs have an m1n1 storage backend is in [bring-up/bootloader/experiments.md](bring-up/bootloader/experiments.md#per-device-menu). The same contract can later be read by m1n1 alone: its Rust side already has GPT and FAT readers.

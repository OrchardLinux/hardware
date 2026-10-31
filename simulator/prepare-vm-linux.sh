#!/bin/sh
# Run inside the Podman Linux VM with sudo. Writes ONLY a new file-backed VM disk.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$ROOT/out/simulator/vm"
ARCHIVE="$ROOT/out/rootfs/0.1.0-next.1/rootfs.tar.zst"
DISK="$OUT/root.raw"
MOUNT="$OUT/mount"
KVER=$(uname -r)
[ "$(id -u)" = 0 ] || { echo 'Must run as root inside the Podman Linux VM' >&2; exit 1; }
[ "$(uname -m)" = aarch64 ] || { echo 'ARM64 Linux required' >&2; exit 1; }
[ -f "$ARCHIVE" ] || { echo 'Public rootfs archive missing' >&2; exit 1; }
[ ! -e "$DISK" ] || { echo "Refusing to overwrite $DISK" >&2; exit 1; }
mkdir -p "$OUT" "$MOUNT"
cleanup() { if mountpoint -q "$MOUNT"; then umount "$MOUNT"; fi; }
trap cleanup EXIT INT TERM
truncate -s 6G "$DISK"
mkfs.ext4 -q -F -L omarchy-vm "$DISK"
mount -o loop "$DISK" "$MOUNT"
tar --zstd --numeric-owner --xattrs --acls -xpf "$ARCHIVE" -C "$MOUNT"
# VM kernel and matching modules are separate from the real iPad kernel.
cp -a "/usr/lib/modules/$KVER" "$MOUNT/usr/lib/modules/"
cp "/usr/lib/modules/$KVER/vmlinuz" "$OUT/vmlinuz"
printf '%s\n' "$KVER" > "$OUT/kernel-version"
# These only gate J71b hardware; preserve the real PAM/logind desktop autologin.
rm -f "$MOUNT/etc/systemd/system/getty@tty1.service.d/20-j71b-platform.conf"
rm -f "$MOUNT/etc/systemd/system/multi-user.target.wants/j71b-charge.service"
rm -f "$MOUNT/etc/systemd/system/multi-user.target.wants/omarchy-diagnostics.service"
printf 'LABEL=omarchy-vm / ext4 defaults 0 1\n' > "$MOUNT/etc/fstab"
# QEMU's DRM output is virtual; no Apple firmware or personalization is copied.
sync
umount "$MOUNT"
rmdir "$MOUNT"
trap - EXIT INT TERM
printf 'VM disk prepared: %s\n' "$DISK"

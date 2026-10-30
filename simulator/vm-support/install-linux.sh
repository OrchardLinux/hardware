#!/bin/sh
# Install only VM support into an existing, stopped simulator disk.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
D="$BASE/out/ipad-simulator/vm"
SUPPORT="$BASE/ipad-simulator/vm-support"
MOUNT="$D/mount"
KVER=$(cat "$D/kernel-version")
[ "$(id -u)" = 0 ] || { echo 'Run as root in the Podman Linux VM' >&2; exit 1; }
[ -f "$D/root.raw" ] && [ -f "$D/orchard-vm-backlight.ko" ] || { echo 'VM disk or matching module missing' >&2; exit 1; }
mkdir -p "$MOUNT"
cleanup() { if mountpoint -q "$MOUNT"; then umount "$MOUNT"; fi; }
trap cleanup EXIT INT TERM
mount -o loop "$D/root.raw" "$MOUNT"
install -D -m 0644 "$D/orchard-vm-backlight.ko" "$MOUNT/usr/lib/modules/$KVER/extra/orchard-vm-backlight.ko"
depmod -b "$MOUNT" "$KVER"
install -D -m 0755 "$SUPPORT/orchard-vm-bridge.py" "$MOUNT/usr/local/libexec/orchard-vm-bridge"
printf '%s\n' orchard-vm-backlight > "$MOUNT/etc/modules-load.d/orchard-vm-backlight.conf"
cat > "$MOUNT/etc/systemd/system/orchard-vm-bridge.service" <<'UNIT'
[Unit]
Description=Orchard simulator virtual hardware bridge
After=systemd-modules-load.service systemd-udevd.service

[Service]
ExecStart=/usr/local/libexec/orchard-vm-bridge
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
UNIT
ln -sfn ../orchard-vm-bridge.service "$MOUNT/etc/systemd/system/multi-user.target.wants/orchard-vm-bridge.service"
sync
umount "$MOUNT"
rmdir "$MOUNT"
trap - EXIT INT TERM
printf 'Installed VM-only backlight and bridge for %s\n' "$KVER"

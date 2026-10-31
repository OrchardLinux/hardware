#!/bin/sh
# Called as root inside the Podman VM while the QEMU disk is stopped.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
D="$BASE/omarchy-next/out/simulator/vm"
SRC="$BASE/omarchy-a/desktop/files"
M="$D/mount"
[ "$(id -u)" = 0 ] || exit 1
mkdir -p "$M"
cleanup() { if mountpoint -q "$M"; then umount "$M"; fi; }
trap cleanup EXIT INT TERM
mount -o loop "$D/root.raw" "$M"
[ -d "$M/usr/share/omarchy-greeter/Commons" ] || { echo 'Install the VM greeter first (its shared theme module is required).' >&2; exit 1; }
install -Dm755 "$D/flea-release/flea" "$M/usr/local/libexec/orchard-flea"
install -Dm644 "$D/flea-release/LICENSE" "$M/usr/local/share/licenses/orchard-flea-preview/LICENSE"
install -d "$M/usr/local/share/orchard-flea" "$M/usr/share/flea" "$M/usr/local/bin" "$M/usr/lib/flea"
rm -rf "$M/usr/local/share/orchard-flea/ui"
cp -R "$D/flea-release/ui" "$M/usr/local/share/orchard-flea/ui"
for config in "$M/usr/local/share/orchard-flea/ui" "$M/usr/local/share/orchard-flea/ui/boot"; do
    for module in Commons Ui; do
        rm -f "$config/$module"
        ln -s "/usr/share/omarchy-greeter/$module" "$config/$module"
    done
done
for tool in flea-gio-auth flea-udisks flea-mpris; do
    install -m 0755 "$D/flea-release/tools/$tool" "$M/usr/lib/flea/$tool"
done
ln -sfn /usr/local/share/orchard-flea/ui "$M/usr/share/flea/ui"
ln -sfn /usr/local/libexec/orchard-flea "$M/usr/local/bin/flea"
install -Dm644 "$SRC/com.orchard.Files.desktop" "$M/usr/local/share/applications/com.orchard.Files.desktop"
sync

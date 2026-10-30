#!/bin/sh
# Install an explicitly VM-only greeter and test PIN into a STOPPED simulator disk.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
D="$BASE/omarchy-next/out/ipad-simulator/vm"
SRC="$BASE/omarchy-a/desktop/greeter"
SUPPORT="$BASE/omarchy-next/ipad-simulator/vm-support"
M="$D/mount"
PKG="$D/greetd.pkg.tar.xz"
[ "$(id -u)" = 0 ] || { echo 'Run as root in the Podman Linux VM' >&2; exit 1; }
[ -f "$PKG" ] && [ -f "$PKG.sig" ] || { echo 'Verified greetd package and signature required' >&2; exit 1; }
mkdir -p "$M"
cleanup() { if mountpoint -q "$M"; then umount "$M"; fi; }
trap cleanup EXIT INT TERM
mount -o loop "$D/root.raw" "$M"
# Verify against the Arch Linux ARM keys and require the pinned trusted signer.
KEYRING=$(mktemp)
trap 'rm -f "$KEYRING"; cleanup' EXIT INT TERM
gpg --dearmor < "$M/usr/share/pacman/keyrings/archlinuxarm.gpg" > "$KEYRING"
grep -q '^68B3537F39A313B3E574D06777193F152BDBE6A6:4:' "$M/usr/share/pacman/keyrings/archlinuxarm-trusted"
VERIFY=$(gpgv --status-fd 1 --keyring "$KEYRING" "$PKG.sig" "$PKG")
printf '%s\n' "$VERIFY" | grep -q '^\[GNUPG:\] VALIDSIG 68B3537F39A313B3E574D06777193F152BDBE6A6 ' || { echo 'Unexpected package signer' >&2; exit 1; }
# Signature is checked above. The greeter provider is our QML, not agreety;
# register greetd in pacman's database without its unused provider dependencies.
install -m 0644 "$PKG" "$M/tmp/orchard-greetd.pkg.tar.xz"
printf '[options]\nSigLevel = Never\nArchitecture = aarch64\n' > "$M/tmp/orchard-pacman.conf"
chroot "$M" /usr/bin/pacman --config /tmp/orchard-pacman.conf -U --nodeps --nodeps --noconfirm /tmp/orchard-greetd.pkg.tar.xz
rm -f "$M/tmp/orchard-greetd.pkg.tar.xz" "$M/tmp/orchard-pacman.conf"
if ! chroot "$M" /usr/bin/getent passwd greeter >/dev/null; then
    chroot "$M" /usr/bin/useradd -r -M -d /var/empty -s /bin/sh -G seat,video greeter
fi
# The package's sysusers hook may already have created greeter without seat access.
chroot "$M" /usr/bin/usermod -a -G seat,video greeter
install -d -m 0755 "$M/usr/share/omarchy-greeter/labwc"
install -m 0644 "$SRC/shell.qml" "$M/usr/share/omarchy-greeter/shell.qml"
install -m 0644 "$SRC/labwc/rc.xml" "$M/usr/share/omarchy-greeter/labwc/rc.xml"
install -m 0755 "$SRC/labwc/autostart" "$M/usr/share/omarchy-greeter/labwc/autostart"
# Update only the VM's corresponding desktop files, not the product archive.
SHELL_SRC="$BASE/omarchy-a/desktop/shell"
SHELL_DST="$M/home/tablet/.local/share/omarchy-ipad"
# The greeter loads the very same lock UI and theme components from a
# system-owned copy, never from the tablet user's writable home directory.
for module in Commons Ui; do
    rm -rf "$M/usr/share/omarchy-greeter/$module"
    cp -R "$SHELL_SRC/shell/$module" "$M/usr/share/omarchy-greeter/$module"
done
install -d -m 0755 "$M/usr/share/omarchy-greeter/plugins/lock" "$M/usr/share/omarchy-greeter/default-theme"
install -m 0644 "$SHELL_SRC/shell/plugins/lock/LockView.qml" "$SHELL_SRC/shell/plugins/lock/LockFeedSurface.qml" "$M/usr/share/omarchy-greeter/plugins/lock/"
install -m 0644 "$SHELL_DST/themes/tokyo-night/colors.toml" "$M/usr/share/omarchy-greeter/default-theme/colors.toml"
BACKGROUND=$(find -L "$SHELL_DST/themes/tokyo-night/backgrounds" -maxdepth 1 -type f \( -iname '*.webp' -o -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' \) | sort | head -n1)
[ -n "$BACKGROUND" ]
install -m 0644 "$BACKGROUND" "$M/usr/share/omarchy-greeter/default-theme/background"
chown -R 0:0 "$M/usr/share/omarchy-greeter"
install -d -m 0755 -o 1000 -g 1000 "$M/var/lib/omarchy-greeter-theme"
install -m 0644 -o 1000 -g 1000 "$SHELL_SRC/shell/Commons/Color.qml" "$SHELL_DST/shell/Commons/Color.qml"
for program in omarchy-greeter-theme-publish omarchy-theme-set omarchy-theme-bg-set; do
    install -m 0755 -o 1000 -g 1000 "$SHELL_SRC/bin/$program" "$SHELL_DST/bin/$program"
    ln -sfn "/home/tablet/.local/share/omarchy-ipad/bin/$program" "$M/usr/local/bin/$program"
done
install -m 0755 "$BASE/omarchy-a/desktop/configuration/first-theme" "$M/usr/local/libexec/arch-ipad-first-theme"
install -m 0755 "$BASE/omarchy-a/desktop/configuration/login-session" "$M/usr/local/libexec/arch-ipad-login-session"
install -m 0644 -o 1000 -g 1000 "$SHELL_SRC/shell/plugins/lock/Service.qml" "$SHELL_DST/shell/plugins/lock/Service.qml"
install -m 0644 -o 1000 -g 1000 "$SHELL_SRC/shell/plugins/lock/LockView.qml" "$SHELL_DST/shell/plugins/lock/LockView.qml"
install -m 0644 -o 1000 -g 1000 "$SHELL_SRC/shell/plugins/services/idle/Service.qml" "$SHELL_DST/shell/plugins/services/idle/Service.qml"
install -m 0755 -o 1000 -g 1000 "$SHELL_SRC/bin/omarchy-tablet-power" "$SHELL_DST/bin/omarchy-tablet-power"
ln -sfn /home/tablet/.local/share/omarchy-ipad/bin/omarchy-tablet-power "$M/usr/local/bin/omarchy-tablet-power"
install -m 0644 -o 1000 -g 1000 "$SHELL_SRC/labwc-rc.xml" "$M/home/tablet/.config/labwc/rc.xml"
install -d -m 0700 "$M/etc/orchard-vm"
if [ ! -e "$M/etc/orchard-vm/power-policy-installed" ]; then
python3 - "$M/home/tablet/.config/omarchy-ipad/shell.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path) as f: config = json.load(f)
idle = config.setdefault('idle', {})
if idle.get('lock', 0) == 0: idle['lock'] = 300
config['disabledPlugins'] = [name for name in config.get('disabledPlugins', []) if name not in ('omarchy.lock', 'omarchy.idle')]
with open(path, 'w') as f: json.dump(config, f, indent=2); f.write('\n')
PY
: > "$M/etc/orchard-vm/power-policy-installed"
fi
install -D -m 0755 "$SRC/greeter-session" "$M/usr/local/libexec/omarchy-greeter-session"
install -D -m 0755 "$SRC/greeter-wake" "$M/usr/local/libexec/omarchy-greeter-wake"
install -D -m 0755 "$SUPPORT/orchard-vm-bridge.py" "$M/usr/local/libexec/orchard-vm-bridge"
install -D -m 0755 "$SUPPORT/check-vm-pin.py" "$M/usr/local/libexec/orchard-vm-check-pin"
install -D -m 0755 "$SUPPORT/vm-pin-service.py" "$M/usr/local/libexec/orchard-vm-pin-service"
cat > "$M/etc/systemd/system/orchard-vm-pin.service" <<'UNIT'
[Unit]
Description=Disposable simulator PIN authentication authority
Before=greetd.service
[Service]
ExecStart=/usr/local/libexec/orchard-vm-pin-service
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
ln -sfn ../orchard-vm-pin.service "$M/etc/systemd/system/multi-user.target.wants/orchard-vm-pin.service"
install -d -m 0700 "$M/etc/orchard-vm"
# Disposable development credential: 123456. Never used for the real product.
# Preserve an existing VM credential when reinstalling UI assets.
if [ ! -e "$M/etc/orchard-vm/pin" ]; then
python3 - "$M/etc/orchard-vm/pin" <<'PY'
import hashlib, os, sys
salt = os.urandom(16)
digest = hashlib.pbkdf2_hmac('sha256', b'123456', salt, 200000)
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'w') as f: f.write(salt.hex() + ':' + digest.hex() + '\n')
PY
fi
chmod 0600 "$M/etc/orchard-vm/pin"
cat > "$M/etc/pam.d/greetd-greeter" <<'PAM'
# The unprivileged greeter is started by greetd without a password.
auth required pam_permit.so
account include system-local-login
session include system-local-login
PAM
cat > "$M/etc/pam.d/greetd" <<'PAM'
# Simulator-only separate PIN; never install this policy in a product image.
auth requisite pam_nologin.so
auth [success=1 default=ignore] pam_exec.so quiet expose_authtok /usr/local/libexec/orchard-vm-check-pin
auth requisite pam_deny.so
auth required pam_permit.so
account include system-local-login
session include system-local-login
PAM
cat > "$M/etc/pam.d/omarchy-lock-password" <<'PAM'
# VM-only: same rate-limited test PIN authority as greetd.
auth [success=1 default=ignore] pam_exec.so quiet expose_authtok /usr/local/libexec/orchard-vm-check-pin
auth requisite pam_deny.so
auth required pam_permit.so
account required pam_permit.so
PAM
cat > "$M/etc/greetd/config.toml" <<'TOML'
[terminal]
vt = 1
[default_session]
command = "/usr/local/libexec/omarchy-greeter-session"
user = "greeter"
TOML
ln -sfn /usr/lib/systemd/system/greetd.service "$M/etc/systemd/system/display-manager.service"
# A shutdown while locked must not make the next VM boot start at 1% brightness.
# The virtual backlight starts at its fixed default instead of persisting zero.
ln -sfn /dev/null "$M/etc/systemd/system/systemd-backlight@backlight:20e200080.backlight.service"
sync
umount "$M"
rm -f "$KEYRING"
rmdir "$M"
trap - EXIT INT TERM
printf 'Installed simulator greeter. Test PIN: 123456 (not the account password).\n'

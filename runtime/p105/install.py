#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Install the P105 hardware overlay into an assembled ARMv7 systemd root."""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile

HERE = Path(__file__).resolve().parent
OVERLAY = HERE / "overlay"
MARKER = "p105-systemd-v1\n"
ENABLE = {
    "multi-user.target.wants/p105-hardware.target": "/usr/lib/systemd/system/p105-hardware.target",
    "sysinit.target.wants/p105-acm-console.service": "/usr/lib/systemd/system/p105-acm-console.service",
}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def arm_elf(path, *, static=False):
    data = path.read_bytes()
    if len(data) < 52 or data[:6] != b"\x7fELF\x01\x01" or struct.unpack_from("<H", data, 18)[0] != 40:
        raise ValueError(f"Expected a 32-bit little-endian ARM ELF: {path}")
    if static:
        phoff = struct.unpack_from("<I", data, 28)[0]
        entsize, count = struct.unpack_from("<HH", data, 42)
        if entsize < 32 or phoff + entsize * count > len(data):
            raise ValueError(f"Invalid ARM program headers: {path}")
        if any(struct.unpack_from("<I", data, phoff + i * entsize)[0] == 3 for i in range(count)):
            raise ValueError(f"Hardware tool must be static: {path}")


def target(root, relative):
    path = root / relative
    if not path.resolve(strict=False).is_relative_to(root):
        raise ValueError(f"Destination escapes the guest root: {relative}")
    return path


def write_file(destination, content, mode):
    """Replace one guest file without following a pre-existing temporary link."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + destination.name + ".p105-", dir=destination.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            shutil.copyfileobj(content, output)
            output.flush()
            os.fchmod(output.fileno(), mode)
            if os.geteuid() == 0:
                os.fchown(output.fileno(), 0, 0)
        os.replace(name, destination)
    finally:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass


def place(source, destination, mode):
    with source.open("rb") as content:
        write_file(destination, content, mode)


def write_text(destination, content):
    write_file(destination, io.BytesIO(content.encode("utf-8")), 0o644)


def install(args):
    root = args.root.resolve()
    source_root = args.initramfs.resolve()
    if root == Path("/") or not root.is_dir():
        raise ValueError("Select an existing guest root directory")
    if os.geteuid() != 0 and not args.allow_user_owned_staging:
        raise ValueError("Run as root, or explicitly select --allow-user-owned-staging then apply guest UID/GID ownership before exporting it")
    arm_elf(target(root, "usr/lib/systemd/systemd"))
    if not target(root, "usr/bin/sh").is_file():
        raise ValueError("The guest root needs /usr/bin/sh")
    marker = target(root, "etc/cascadia/systemd-root")
    if marker.exists() and marker.read_text() != MARKER:
        raise ValueError("An incompatible Cascadia root marker already exists")

    if args.calibration.is_symlink():
        raise ValueError("The explicit calibration file must not be a symlink")
    calibration = args.calibration.resolve()
    if calibration.is_relative_to(source_root):
        raise ValueError("Select your own calibration file outside the selected initramfs tree")
    if not calibration.is_file() or calibration.stat().st_size == 0:
        raise ValueError("Select the nonempty calibration file extracted from your own iPad")
    calibration_data = calibration.read_bytes()

    payload = []
    for line in (OVERLAY / "usr/lib/cascadia/p105-payload.list").read_text().splitlines():
        source, destination, mode = line.split("|")
        src = source_root / source
        if not src.resolve(strict=False).is_relative_to(source_root):
            raise ValueError(f"Payload source escapes the selected initramfs: {source}")
        if not src.is_file() or src.stat().st_size == 0:
            raise ValueError(f"Missing current-boot payload: {src}")
        if source.endswith(("z2-boot", "p105-keys", "busybox.static")):
            arm_elf(src, static=True)
        if source == "lib/firmware/mtcal.bin":
            if src.read_bytes() != calibration_data:
                raise ValueError("The initramfs calibration differs from --calibration; rebuild the boot image with your own calibration")
            src = calibration
        if source == "sbin/p105-keys":
            data = src.read_bytes()
            if b"--desktop-home\0" not in data or b"Cascadia P105 Home\0" not in data:
                raise ValueError("p105-keys needs the desktop Home patch; rebuild the external Cascadia initramfs")
        payload.append((src, destination, int(mode, 8)))

    installed = {}
    for src in sorted(OVERLAY.rglob("*")):
        if src.is_symlink():
            raise ValueError(f"Unexpected source overlay symlink: {src}")
        if not src.is_file():
            continue
        relative = src.relative_to(OVERLAY).as_posix()
        dst = target(root, relative)
        place(src, dst, 0o755 if relative.startswith("usr/libexec/p105/") else 0o644)
        installed[relative] = sha(dst)
    payload_hashes = {}
    for src, relative, mode in payload:
        dst = target(root, relative)
        place(src, dst, mode)
        payload_hashes[relative] = sha(dst)
    checksum_file = target(root, "usr/lib/cascadia/p105-payload.sha256")
    write_text(checksum_file, "".join(f"{digest}  {name}\n" for name, digest in payload_hashes.items()))

    for relative, link_target in ENABLE.items():
        link = root / "etc/systemd/system" / relative
        if not link.parent.resolve(strict=False).is_relative_to(root):
            raise ValueError(f"Unit directory escapes the guest root: {link.parent}")
        link.parent.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            if os.readlink(link) != link_target:
                raise ValueError(f"An incompatible service link already exists: {link}")
        elif link.exists():
            raise ValueError(f"A file occupies the service link path: {link}")
        else:
            link.symlink_to(link_target)
    for name in ("proc", "sys", "dev", "run", "tmp"):
        directory = target(root, name)
        directory.mkdir(exist_ok=True)
        directory.chmod(0o1777 if name == "tmp" else 0o755)
    record = {
        "schema": 1,
        "device": "iPad2,5 / p105ap",
        "root_marker": MARKER.strip(),
        "init": "/usr/libexec/p105/systemd-init",
        "hardware_activation": "systemd owns the static Cascadia touch/key tools with opt-in Home forwarding, the bounded Wi-Fi EHCI recovery and the native Bluetooth daemon",
        "private_inputs": "firmware comes from the selected verified Cascadia initramfs; calibration comes from the explicit matching user file; no private payload is bundled",
        "payload": payload_hashes,
        "overlay": installed,
        "enabled_units": ENABLE,
        "guest_root_ownership_applied": os.geteuid() == 0,
    }
    manifest = target(root, "usr/lib/cascadia/p105-runtime.json")
    write_text(manifest, json.dumps(record, indent=2) + "\n")
    print("Installed P105 systemd hardware runtime:", root)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--initramfs", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True,
                        help="Regular external calibration file from your own iPad; must match the selected initramfs")
    parser.add_argument("--allow-user-owned-staging", action="store_true")
    args = parser.parse_args()
    try:
        install(args)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"p105-runtime: {error}\n")


if __name__ == "__main__":
    main()

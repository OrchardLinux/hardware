#!/usr/bin/env python3
"""Check public package resources and refuse build scratch or archive links."""
import argparse
import json
from pathlib import Path, PurePosixPath
import stat
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def resources(source):
    files = {source / name for name in (
        '__init__.py', 'buildenv.py',
        'kernel/Containerfile', 'kernel/install-toolchain', 'kernel/build',
        'kernel/build-modules', 'kernel/stage-modules.py', 'kernel/write-receipt.py',
        'kernel/series.py', 'kernel/sources.toml', 'kernel/upstream.lock',
        'boot/m1n1/Containerfile', 'boot/m1n1/install-toolchain', 'boot/m1n1/build',
        'boot/m1n1/series', 'boot/m1n1/sources.toml', 'boot/m1n1/upstream.lock')}
    for pattern in ('features.toml', 'apple/*.py', 'image/*.py', 'launcher/*.py',
                    'runtime/j71b/*.py', 'runtime/j71b/phase2-hardware',
                    'devices/*/device.toml', 'devices/*/firmware.json',
                    'devices/*/public-firmware/*', 'schemas/v1/*.json',
                    'boot/__init__.py', 'boot/initramfs/*.py',
                    'boot/initramfs/busybox/*.py', 'boot/initramfs/busybox/Containerfile',
                    'boot/initramfs/recovery/*.json', 'boot/initramfs/recovery/Containerfile',
                    'boot/initramfs/recovery/init', 'boot/initramfs/recovery/usb-gadget',
                    'boot/initramfs/recovery/authorize-key', 'boot/initramfs/recovery/start-ssh',
                    'boot/initramfs/recovery/capture-adt', 'boot/initramfs/recovery/udhcpd.conf',
                    'boot/initramfs/recovery/upstream.lock', 'boot/initramfs/recovery/build-tools*',
                    'boot/initramfs/recovery/install-toolchain',
                    'boot/quiet/*.py', 'boot/quiet/*.patch',
                    'kernel/series/*', 'kernel/configs/*/*', 'kernel/patches/**/*.patch',
                    'kernel/devicetree/*/*', 'kernel/modules/*/*/*',
                    'boot/m1n1/patches/**/*.patch',
                    'docs/bring-up/*.md',
                    'boot/initramfs/nfs/*'):
        files.update(path for path in source.glob(pattern) if path.is_file())
    return sorted(files)


def member_name(name):
    path = PurePosixPath(name)
    if '\\' in name or path.is_absolute() or '..' in path.parts or not path.parts:
        raise ValueError('Unsafe archive path: ' + name)
    if any(part in ('.work', '__pycache__') for part in path.parts):
        raise ValueError('Build scratch in public package: ' + name)
    return path


def audit(archive, source=ROOT):
    source = Path(source).resolve()
    archive = Path(archive)
    if archive.suffix == '.whl':
        container = zipfile.ZipFile(archive)
        entries = container.infolist()
        names = [entry.filename for entry in entries]
        sizes = {entry.filename: entry.file_size for entry in entries}
        links = [entry.filename for entry in entries
                 if stat.S_ISLNK(entry.external_attr >> 16)]
        prefix = 'orchard_hardware/'
        read = container.read
    else:
        container = tarfile.open(archive, 'r:gz')
        entries = container.getmembers()
        names = [entry.name for entry in entries]
        sizes = {entry.name: entry.size for entry in entries}
        links = [entry.name for entry in entries if entry.issym() or entry.islnk()]
        roots = {part for name in names for part in PurePosixPath(name).parts[:1]}
        if len(roots) != 1:
            container.close()
            raise ValueError('Source package needs one enclosing directory')
        prefix = next(iter(roots)) + '/'

        def read(name):
            with container.extractfile(name) as stream:
                return stream.read()

    with container:
        for name in names:
            member_name(name)
        if links:
            raise ValueError('Archive link in public package: ' + links[0])
        if len(names) != len(set(names)):
            raise ValueError('Duplicate archive member')
        required = resources(source)
        for path in required:
            name = prefix + path.relative_to(source).as_posix()
            if name not in sizes:
                raise ValueError('Missing package resource: ' + name)
            if sizes[name] != path.stat().st_size or read(name) != path.read_bytes():
                raise ValueError('Changed package resource: ' + name)
    return {'archive': str(archive), 'members': len(names), 'verified_resources': len(required)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', nargs='+', type=Path)
    parser.add_argument('--source', type=Path, default=ROOT)
    args = parser.parse_args()
    for archive in args.archives:
        print(json.dumps(audit(archive, args.source), sort_keys=True))


if __name__ == '__main__':
    main()

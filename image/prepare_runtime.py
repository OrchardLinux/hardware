#!/usr/bin/env python3
"""Stage finished public recovery, including Python, modules and device runtime."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tarfile

from orchard_hardware import firmware_path
from .runtime import files as runtime_files
from .integrity import sha
from .elf import needed

PROJECT = Path(__file__).resolve().parents[1]
HERE = PROJECT / 'boot/initramfs/j71b/recovery'


def copy(source, target, mode):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def stage_runtime(base, receipt, root):
    spec = json.loads(receipt.read_text())
    if base.stat().st_size != spec['bytes'] or sha(base) != spec['sha256']:
        raise ValueError('Python source root differs from receipt')
    libraries = re.compile(r'usr/lib/(?:ld-linux-aarch64|lib(?:python[0-9.]+|c|m|z|zstd|bz2|lzma|ffi|ssl|crypto|readline|ncursesw|tinfo|uuid|expat|gcc_s|mpdec))[-.].*')
    with tarfile.open(base, 'r|gz') as archive:
        for member in archive:
            name = member.name.removeprefix('./')
            tool = re.fullmatch(r'usr/(?:sbin|bin)/(blkid|sfdisk|mke2fs)', name)
            selected = (tool or name == 'etc/mke2fs.conf' or
                        re.fullmatch(r'usr/bin/python3(?:\.[0-9]+)?', name) or
                        re.match(r'usr/lib/python3\.[0-9]+(?:/|$)', name) or libraries.fullmatch(name))
            if (not selected or any('/' + component + '/' in '/' + name + '/' for component in
                                    ('site-packages', 'test', 'tests', '__pycache__', 'idlelib', 'tkinter')) or
                    ('/lib-dynload/' in name and Path(name).name.startswith(
                        ('_tkinter', '_curses', '_test', '_ctypes_test', '_dbm', '_gdbm', '_sqlite3', 'readline')))):
                continue
            if tool:
                if not member.isfile():
                    raise ValueError('Recovery storage tools must be native executables')
                name = 'usr/sbin/' + tool[1]
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if member.isdir():
                path.mkdir(exist_ok=True)
            elif member.issym():
                if not path.exists() and not path.is_symlink():
                    path.symlink_to(member.linkname)
            elif member.isfile():
                with path.open('wb') as output:
                    shutil.copyfileobj(archive.extractfile(member), output)
                path.chmod(member.mode)
            elif member.islnk():
                source = root / member.linkname.removeprefix('./')
                if not source.is_file():
                    raise ValueError('Python runtime hardlink target is missing')
                shutil.copyfile(source, path)
                path.chmod(member.mode)
    if not (root / 'usr/bin/python3').exists():
        raise ValueError('Python runtime is incomplete')
    for name in ('blkid', 'sfdisk', 'mke2fs'):
        tool = root / 'usr/sbin' / name
        if not tool.is_file() or tool.read_bytes()[:4] != b'\x7fELF' or not tool.stat().st_mode & 0o111:
            raise ValueError('Recovery storage tool is missing or not native: ' + name)
    if not (root / 'etc/mke2fs.conf').is_file():
        raise ValueError('Recovery requires the base mke2fs configuration')
    # Resolve the complete ELF closure, including dependencies of extension
    # modules. A filename whitelist alone silently misses distro changes.
    for attempt in range(12):
        required = set()
        for path in (root / 'usr').rglob('*'):
            if path.is_file() and not path.is_symlink():
                required.update(needed(path.read_bytes()))
        missing = set()
        for name in required:
            path = root / 'usr/lib' / name
            for _ in range(12):
                if not path.is_symlink():
                    break
                target = os.readlink(path)
                path = root / target.lstrip('/') if target.startswith('/') else path.parent / target
            if not path.is_file():
                missing.add(path.name)
                missing.add(name)
        if not missing:
            return
        copied = False
        with tarfile.open(base, 'r|gz') as archive:
            for member in archive:
                name = member.name.removeprefix('./')
                if not name.startswith('usr/lib/') or '/' in name[len('usr/lib/'):] or Path(name).name not in missing:
                    continue
                path = root / name
                if member.issym():
                    target = member.linkname
                    missing.add(Path(target).name)
                    if target.startswith('/'):
                        target = os.path.relpath(target, '/' + str(Path(name).parent))
                    if not path.is_symlink() and not path.exists():
                        path.symlink_to(target)
                        copied = True
                elif member.isfile():
                    path.write_bytes(archive.extractfile(member).read())
                    path.chmod(member.mode)
                    copied = True
        if not copied:
            raise ValueError('Unresolved Python runtime libraries: ' + ', '.join(sorted(missing)))
    raise ValueError('Python runtime dependency closure did not converge')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('busybox', 'dropbear', 'dropbearkey', 'base', 'base-receipt', 'modules-dir', 'policy-dir', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    purpose = parser.add_mutually_exclusive_group(required=True)
    purpose.add_argument('--root-receipt', type=Path)
    purpose.add_argument('--launcher-catalogue', type=Path)
    parser.add_argument('--launcher-release')
    parser.add_argument('--cleanup-receipt', type=Path, action='append', default=[])
    args = parser.parse_args()
    cleanup_receipts = []
    for receipt in args.cleanup_receipt:
        previous = json.loads(receipt.read_text())
        if (previous.get('kind') != 'rootfs' or previous.get('profile') != 'j71b' or
                previous.get('schema') != 1 or not previous.get('release') or
                not re.fullmatch('[0-9a-f]{64}', previous.get('sha256', ''))):
            raise ValueError('Invalid compatible cleanup receipt')
        cleanup_receipts.append({'release': previous['release'], 'sha256': previous['sha256']})
    root = args.output
    if root.exists():
        parser.error('Choose a new staging directory')
    for name in ('bin', 'sbin', 'usr/bin', 'usr/sbin', 'usr/lib', 'etc', 'root', 'proc', 'sys', 'dev', 'run', 'tmp'):
        (root / name).mkdir(parents=True, exist_ok=True)
    (root / 'lib').symlink_to('usr/lib')
    for source, name in ((args.busybox, 'bin/busybox'), (args.dropbear, 'usr/sbin/dropbear'),
                         (args.dropbearkey, 'usr/bin/dropbearkey')):
        copy(source, root / name, 0o755)
    stage_runtime(args.base, args.base_receipt, root)
    kernel_release, public = runtime_files(args.modules_dir, args.policy_dir)
    for name, (data, mode) in public.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)
    if args.launcher_catalogue:
        catalogue = json.loads(args.launcher_catalogue.read_text())
        if (not args.launcher_release or catalogue.get('schema') != 1 or
                catalogue.get('profile') != 'j71b' or not catalogue.get('installations') or
                any(r['kernel_release'] != kernel_release for r in catalogue['installations'])):
            raise ValueError('Invalid launcher catalogue/kernel identity')
        copy(args.launcher_catalogue, root / 'etc/orchard/launch.json', 0o644)
        configuration = {'protocol':1, 'release':args.launcher_release, 'profile':'j71b',
                         'kernel_release':kernel_release, 'mode':'launcher'}
    else:
        spec = json.loads(args.root_receipt.read_text())
        if spec['kernel_release'] != kernel_release:
            raise ValueError('Root/recovery kernel releases differ')
        configuration = {'protocol': 1, 'release': spec['release'], 'profile': 'j71b',
                         'kernel_release': kernel_release, 'cleanup_receipts': cleanup_receipts,
                         'root': {k: spec[k] for k in ('sha256', 'bytes', 'expanded_bytes', 'members', 'compression')}}
    (root / 'etc/orchard/release.json').write_text(json.dumps(configuration))
    copy(firmware_path('j71b'), root / 'etc/orchard/firmware.json', 0o644)
    for source, name in (('init', 'init'), ('usb-gadget', 'sbin/recovery-usb'),
                         ('authorize-key', 'sbin/authorize-key'), ('start-ssh', 'sbin/recovery-start-ssh'),
                         ('capture-adt', 'sbin/capture-adt')):
        copy(HERE / source, root / name, 0o755)
    copy(HERE / 'udhcpd.conf', root / 'etc/udhcpd.conf', 0o644)
    (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\n')
    (root / 'etc/group').write_text('root:x:0:\n')
    (root / 'etc/shadow').write_text('root:!:0:0:99999:7:::\n')
    (root / 'etc/shadow').chmod(0o600)
    print('Public recovery staged:', root)


if __name__ == '__main__':
    main()

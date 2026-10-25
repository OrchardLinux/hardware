#!/usr/bin/env python3
"""Destructive ONLY to owned temporary loop images in an isolated Linux VM."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/j71b'))
import storage


def command(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(str(args) + ': ' + result.stderr.decode(errors='replace'))
    return result.stdout.decode().strip()


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run only in an isolated rootful Linux test container')
    with tempfile.TemporaryDirectory(prefix='orchard-storage-') as temp:
        root = Path(temp)
        disk = root / 'disk.img'
        with disk.open('xb') as stream:
            stream.truncate(256 * 1024**2)
        layout = ('label: gpt\n'
                  'size=64M,type=linux,name="Existing"\n'
                  'size=128M,type=linux,name="New"\n'
                  'size=16M,type=7c3457ef-0000-11aa-aa11-00306543ecac,name="Linux"\n'
                  'size=16M,type=linux,name="Unsupported"\n'
                  'size=16M,type=linux,name="Failure"\n')
        command('/usr/sbin/sfdisk', str(disk), input=layout.encode())
        # Rootful VM containers may expose loop-control but no unused nodes.
        available = command('/bin/busybox', 'losetup', '-f').split()[0]
        if not Path(available).exists():
            os.mknod(available, 0o600 | 0o060000, os.makedev(7, int(available.removeprefix('/dev/loop'))))
        loop = available
        command('/bin/busybox', 'losetup', '-P', loop, str(disk))
        storage.DISK = Path(loop)
        mounted = None
        try:
            backing = Path('/' + (storage.SYS / storage.DISK.name / 'loop/backing_file').read_text().strip().lstrip('/'))
            if backing.resolve() != disk.resolve():
                raise RuntimeError('Refusing a loop device not backed by our temporary image')
            for index in range(1, 6):
                path = Path(loop + 'p' + str(index))
                deadline = time.monotonic() + 5
                while not (storage.SYS / path.name).exists():
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Test partition discovery timed out')
                    time.sleep(0.02)
                if not path.exists():
                    major, minor = map(int, (storage.SYS / path.name / 'dev').read_text().split(':'))
                    os.mknod(path, 0o600 | 0o060000, os.makedev(major, minor))
            parts = [Path(loop + 'p' + str(n)) for n in range(1, 6)]
            command('/usr/sbin/mke2fs', '-q', '-t', 'ext4', '-E', 'nodiscard', str(parts[0]))
            command('/usr/sbin/mke2fs', '-q', '-t', 'ext3', '-E', 'nodiscard', str(parts[3]))
            mount = root / 'mount'
            mount.mkdir()
            command('mount', str(parts[0]), str(mount))
            mounted = mount
            for name in ('omarchy', 'orchard-test'):
                (mount / name).mkdir()
                (mount / name / 'preserved').write_text(name)
            command('umount', str(mount))
            mounted = None
            with parts[2].open('r+b', buffering=0) as stream:
                stream.seek(8192)
                stream.write(b'non-Linux partition must remain unchanged')
            for path in parts:
                command('blockdev', '--setro', str(path))
            before = {str(path): digest(path) for path in (parts[0], parts[2], parts[3])}
            with disk.open('rb') as stream:
                front = hashlib.sha256(stream.read(1024**2)).hexdigest()
                stream.seek(-1024**2, 2)
                back = hashlib.sha256(stream.read()).hexdigest()
            rows = storage.inventory()
            assert len(rows) == 4 and not any(p['path'] == str(parts[2]) for p in rows)
            target = next(p for p in rows if p['path'] == str(parts[1]))
            assert target['filesystem'] == 'unformatted', (target, storage.probe(parts[1]))
            fsuuid = str(uuid.uuid4())
            formatted = storage.format_ext4(target, fsuuid)
            assert formatted['filesystem'] == 'ext4' and formatted['filesystem_uuid'] == fsuuid
            assert command('blockdev', '--getro', str(parts[1])) == '1'
            for row in (target, next(p for p in rows if p['path'] == str(parts[0])),
                        next(p for p in rows if p['path'] == str(parts[3]))):
                try:
                    storage.format_ext4(row, str(uuid.uuid4()))
                except ValueError:
                    pass
                else:
                    raise AssertionError('Reformatted an existing or changed filesystem')
            # A command failure must close the write window, even without a
            # successful mkfs result. The actual successful mkfs above is real.
            failed = next(p for p in rows if p['path'] == str(parts[4]))
            actual = storage.checked
            def timeout(*args, **kwargs):
                if args[0] == str(storage.TOOLS / 'mke2fs'):
                    raise subprocess.TimeoutExpired(args, 120)
                return actual(*args, **kwargs)
            with patch.object(storage, 'checked', side_effect=timeout):
                try:
                    storage.format_ext4(failed, str(uuid.uuid4()))
                except subprocess.TimeoutExpired:
                    pass
                else:
                    raise AssertionError('Missing simulated timeout')
            assert command('blockdev', '--getro', str(parts[4])) == '1'
            command('blockdev', '--setrw', str(parts[1]))
            command('mount', str(parts[1]), str(mount))
            mounted = mount
            (mount / 'minimal').mkdir()
            (mount / 'minimal/payload').write_bytes(b'installed test payload')
            try:
                storage.format_ext4(formatted, str(uuid.uuid4()))
            except ValueError:
                pass
            else:
                raise AssertionError('Accepted a mounted partition')
            command('umount', str(mount))
            mounted = None
            command('blockdev', '--setro', str(parts[1]))
            if Path('/usr/sbin/e2fsck').is_file():
                command('/usr/sbin/e2fsck', '-f', '-n', str(parts[1]))
            assert all(digest(Path(path)) == expected for path, expected in before.items())
            with disk.open('rb') as stream:
                assert hashlib.sha256(stream.read(1024**2)).hexdigest() == front
                stream.seek(-1024**2, 2)
                assert hashlib.sha256(stream.read()).hexdigest() == back
            print(json.dumps({'formatted': 'ext4', 'verified_uuid': fsuuid,
                              'preserved_existing_roots_and_other_partitions': True,
                              'gpt_unchanged': True, 'timeout_refenced': True,
                              'existing_changed_and_mounted_targets_refused': True}))
        finally:
            if mounted is not None:
                command('umount', str(mounted))
            command('/bin/busybox', 'losetup', '-d', loop)


if __name__ == '__main__':
    main()

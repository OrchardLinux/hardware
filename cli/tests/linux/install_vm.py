#!/usr/bin/env python3
"""Run install_device.py against a loop-device disk in a Linux VM, as root.

Not part of the unit tests (needs root and loop devices). From macOS:
    podman machine ssh "sudo python3 $PWD/tests/linux/install_vm.py $PWD STATIC_ARM64_BUSYBOX"
Covers a free range, replacing a root, a stale plan and the old one-partition layout,
and the account step: password, lock-screen PIN file, PAM service and PIN check.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import uuid

CLI = Path(sys.argv[1])
BUSYBOX = Path(sys.argv[2])
sys.path.insert(0, str(CLI / 'src'))
from orchard_cli import install_layout as L          # noqa: E402
from orchard_cli.bootdisk import Disk, read_boot     # noqa: E402

DEVICE = CLI / 'src/orchard_cli/install_device.py'
APFS = '7c3457ef-0000-11aa-aa11-00306543ecac'
VERSION = '7.3.0-rc1-orchard-apple-16k'
GIB = 1 << 30
BOARD_DTB = 't8010-j171.dtb'           # an iPad 7, not the J71b


def sh(*argv, **kwargs):
    return subprocess.run(argv, check=True, capture_output=True, **kwargs).stdout


def events(output):
    return [json.loads(line) for line in output.decode().splitlines() if line.startswith('{')]


def device(disk, *args, stdin=b''):
    result = subprocess.run([sys.executable, str(DEVICE), *args], input=stdin, capture_output=True,
                            env={**os.environ, 'ORCHARD_INSTALL_DISK': disk})
    found = events(result.stdout)
    if result.returncode:
        raise SystemExit(f'device {args[0]} failed: {found[-1] if found else result.stderr.decode()}')
    return found


def root_image():
    kernel_install = f'''#!/bin/sh
export PATH=/bin
ver=$2; kernel=$3; token=$(cat /etc/kernel/entry-token); dir=/boot/$token/$ver
dtb=$(cat /etc/kernel/devicetree)
mkdir -p $dir /boot/loader/entries
cp $kernel $dir/linux; echo initrd > $dir/initrd; cp /usr/lib/modules/$ver/dtb/$dtb $dir/
printf 'title Test\\nversion %s\\nsort-key test\\nlinux /%s/%s/linux\\ninitrd /%s/%s/initrd\\ndevicetree /%s/%s/%s\\noptions %s\\n' \\
  $ver $token $ver $token $ver $token $ver $dtb "$(cat /etc/kernel/cmdline)" > /boot/loader/entries/$token-$ver.conf
'''
    files = {'bin/busybox': (BUSYBOX.read_bytes(), 0o755),
             'usr/bin/kernel-install': (kernel_install.encode(), 0o755),
             f'usr/lib/modules/{VERSION}/vmlinuz': (b'ARM64 kernel image' * 100, 0o644),
             # One root for every board of the platform: the plan picks the DTB.
             f'usr/lib/modules/{VERSION}/dtb/t8010-j71b.dtb': (b'\xd0\x0d\xfe\xed' + bytes(60), 0o644),
             f'usr/lib/modules/{VERSION}/dtb/t8010-j171.dtb': (b'\xd0\x0d\xfe\xed' + bytes(61), 0o644),
             'etc/os-release': (b'ID=test\n', 0o644),
             'etc/passwd': (b'root:x:0:0::/root:/bin/sh\ntablet:x:1000:1000::/home/tablet:/bin/sh\n', 0o644),
             'etc/group': (b'root:x:0:\ntablet:x:1000:\n', 0o644),
             'etc/shadow': (b'root:!:1::::::\ntablet:!:1::::::\n', 0o600),
             # Busybox has no chpasswd; this stands in for shadow's, hashing like it does.
             'usr/bin/chpasswd': (b'#!/bin/sh\nIFS=: read -r user password\n'
                                  b'printf "root:!:1::::::\\n%s:%s:1::::::\\n" "$user" '
                                  b'"$(printf %s "$password" | openssl passwd -6 -stdin)" > /etc/shadow\n', 0o755),
             **host_openssl()}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, (data, mode) in files.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), mode
            tar.addfile(info, io.BytesIO(data))
        for link in ('lib', 'lib64'):                      # Arch: /lib is a link to usr/lib
            info = tarfile.TarInfo(link)
            info.type, info.linkname = tarfile.SYMTYPE, 'usr/' + link
            tar.addfile(info)
        for applet in ('sh', 'mkdir', 'cp', 'cat', 'printf', 'echo', 'tr'):
            info = tarfile.TarInfo('bin/' + applet)
            info.type, info.linkname = tarfile.SYMTYPE, 'busybox'
            tar.addfile(info)
    data = buffer.getvalue()
    return data, {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
                  'expanded_bytes': 4 << 20, 'compression': 'gzip'}


def host_openssl():
    """The VM's openssl and its libraries, at the same paths, for the account step."""
    paths = ['/usr/bin/openssl'] + [word for line in sh('ldd', '/usr/bin/openssl').decode().splitlines()
                                    for word in line.split() if word.startswith('/')]
    # Merged /usr like Arch and Fedora: /lib and /lib64 are links into usr/.
    return {'usr/' + path.lstrip('/').removeprefix('usr/'): (Path(path).read_bytes(), 0o755) for path in paths}


def check_account(disk, root):
    """The installed account: hashed password, PIN file and the PIN check itself."""
    target = Path('/run/orchard/check')
    target.mkdir(parents=True, exist_ok=True)
    sh('mount', '-o', 'ro', root, str(target))
    try:
        shadow = (target / 'etc/shadow').read_text()
        assert 'tablet:$6$' in shadow and 'secret password' not in shadow, shadow
        pin = target / 'etc/orchard/pin'
        assert pin.read_text().startswith('tablet:$6$') and '424242' not in pin.read_text()
        assert (pin.stat().st_mode & 0o777, pin.stat().st_uid, pin.stat().st_gid) == (0o640, 0, 1000)
        tally = (target / 'var/lib/orchard/pin-tally').stat()
        assert (tally.st_mode & 0o777, tally.st_uid) == (0o700, 1000)
        assert 'pin-check' in (target / 'etc/pam.d/omarchy-lock-password').read_text()
        assert (target / 'etc/pam.d/greetd').read_text() == (target / 'etc/pam.d/omarchy-lock-password').read_text()
        assert (target / 'var/lib/orchard/pin-tally/tablet').stat().st_uid == 1000
        firmware = target / 'usr/lib/firmware/apple/test.bin'
        assert firmware.read_bytes() == FIRMWARE_BYTES and firmware.stat().st_mode & 0o777 == 0o644
        assert not (target / 'mixin.json').exists()

        def pin_ok(value):
            return subprocess.run(['chroot', str(target), '/usr/lib/orchard/pin-check'], input=value + b'\0',
                                  env={'PAM_USER': 'tablet'}, capture_output=True).returncode == 0
        assert pin_ok(b'424242') and not pin_ok(b'424243') and not pin_ok(b'42424')
    finally:
        sh('umount', str(target))


FIRMWARE_BYTES = b'private firmware' * 64


def write_firmware():
    """A bundle shaped like the CLI's firmware mixin, uploaded to /run/orchard."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w:gz') as tar:
        for name, data in (('lib/firmware/apple/test.bin', FIRMWARE_BYTES), ('mixin.json', b'{}')):
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            tar.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    path = Path('/run/orchard/firmware.tar.gz')
    path.write_bytes(data)
    return {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


def write_secrets():
    path = Path('/run/orchard/secrets.json')
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump({'password': 'secret password', 'pin': '424242'}, out)
    return path


def apfs_digest(disk):
    return hashlib.sha256(sh('dd', f'if={disk}p1', 'bs=1M', 'count=8', 'status=none')).hexdigest()


def install(disk, choose, image, data, label):
    (layout,) = [e for e in device(disk, 'inspect') if e['event'] == 'layout']
    option = choose(L.options(layout))
    Path('/run/orchard').mkdir(parents=True, exist_ok=True)
    plan = L.plan(layout, option, image, {'id': 'test', 'options': 'quiet', 'devicetree': BOARD_DTB}, account=True,
                  firmware=write_firmware())
    with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
        json.dump(plan, f)
    secrets = write_secrets()
    found = device(disk, 'run', f.name, stdin=data)
    assert not secrets.exists(), 'secrets file left behind'
    assert not any(word in json.dumps(found) + Path('/run/orchard/install.log').read_text()
                   for word in ('secret password', '424242')), 'a secret reached the events or log'
    print(label, [e.get('step') or e['event'] for e in found if e['event'] in ('step', 'finished')])
    (finished,) = [e for e in found if e['event'] == 'finished']
    numbers = {p['partuuid']: p['number'] for p in
               [e for e in device(disk, 'inspect') if e['event'] == 'layout'][0]['partitions']}
    check_account(disk, f'{disk}p{numbers[finished["root"]]}')
    return plan


def main():
    with tempfile.TemporaryDirectory(dir='/var/tmp') as work:
        backing = Path(work) / 'disk.img'
        with backing.open('wb') as f:
            f.truncate(16 * GIB)
        disk = sh('losetup', '--sector-size', '4096', '--show', '-fP', str(backing)).decode().strip()
        sh('blockdev', '--setrw', disk)   # a previous run's read-only fence stays on the loop node
        try:
            sh('sfdisk', disk, input=f'label: gpt\nstart=256, size={6 * GIB // 4096}, type={APFS}\n'.encode())
            sh('blockdev', '--rereadpt', disk)
            sh('dd', 'if=/dev/urandom', f'of={disk}p1', 'bs=1M', 'count=8', 'status=none')
            before = apfs_digest(disk)
            data, image = root_image()
            L.MIN_ROOT_BYTES = 2 * GIB                     # small test disk; rules unchanged

            first = install(disk, lambda options: [o for o in options if o.kind == 'free'][0],
                            image, data, 'case a (free space):')
            second = install(disk, lambda options: [o for o in options if o.kind == 'replace'][0],
                             image, data, 'case c (replace root):')
            assert apfs_digest(disk) == before, 'APFS bytes changed'
            with open(disk, 'rb') as dev:
                def read(lba):
                    dev.seek(lba * 4096)
                    return dev.read(4096)
                _, _, entries = read_boot(Disk(read, 4096), BOARD_DTB)
            assert [e.problems for e in entries] == [[]], [e.problems for e in entries]
            assert entries[0].root == second['root']['partuuid'] != first['root']['partuuid']
            print('APFS untouched; boot entries:', [(e.id, e.root[:8]) for e in entries], '- contract valid')

            # A plan from an older inspect must be refused before any write.
            with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
                json.dump(first, f)
            write_secrets()
            result = subprocess.run([sys.executable, str(DEVICE), 'run', f.name], input=data, capture_output=True,
                                    env={**os.environ, 'ORCHARD_INSTALL_DISK': disk})
            refused = events(result.stdout)[-1]
            assert result.returncode and refused['code'] == 'layout-changed', refused
            print('stale plan refused:', refused['code'])

            # Case b: the old layout, one ext4 Linux-data partition over all free space.
            start = 256 + 6 * GIB // 4096
            sh('blockdev', '--setrw', disk)
            sh('sfdisk', disk, input=(f'label: gpt\nstart=256, size={6 * GIB // 4096}, type={APFS}\n'
                                      f'start={start}, type=0fc63daf-8483-4772-8e79-3d69d8477de4\n').encode())
            sh('blockdev', '--rereadpt', disk)
            sh('dd', 'if=/dev/urandom', f'of={disk}p1', 'bs=1M', 'count=8', 'status=none')
            sh('mke2fs', '-q', '-t', 'ext4', '-L', 'OrchardLinux', f'{disk}p2')
            before = apfs_digest(disk)
            install(disk, lambda options: [o for o in options if o.kind == 'replace'][0],
                    image, data, 'case b (replace old layout):')
            assert apfs_digest(disk) == before, 'APFS bytes changed'
            with open(disk, 'rb') as dev:
                def read(lba):
                    dev.seek(lba * 4096)
                    return dev.read(4096)
                parts, _, entries = read_boot(Disk(read, 4096), BOARD_DTB)
            types = sorted(str(p.type_guid)[:8] for p in parts)
            assert [e.problems for e in entries] == [[]] and types == ['7c3457ef', 'b921b045', 'bc13c2ff'], types
            print('case b: APFS untouched, old partition replaced by boot + root, entry valid')
        finally:
            subprocess.run(['losetup', '-d', disk])


if __name__ == '__main__':
    main()

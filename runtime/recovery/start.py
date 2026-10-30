#!/usr/bin/python3
"""Mount an installed root so the recovery can start it in this boot.

The recovery's PID 1 (init) runs this when `orchard-device start` has asked for
a root in /run/orchard/start.json, and switches root on success. Only a root
whose boot entry names exactly the running kernel qualifies: the recovery and
the root come from the same kernel build, so the root starts as it would from
its entry, without another DFU boot to reload the same kernel. Otherwise it
has to be started from its entry: `orchard launch`.
"""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import uuid

REQUEST = Path('/run/orchard/start.json')
NEWROOT = Path('/newroot')
BOOT = Path('/run/orchard/start-boot')
SYSFS = Path('/sys/class/block')


def run(*argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{argv[0]}: {result.stderr.strip()[-500:]}')


def gpt_partuuid(disk, sector, number):
    """The unique GUID of GPT entry NUMBER on DISK, or None."""
    with open(disk, 'rb') as stream:
        stream.seek(sector)
        header = stream.read(92)
        if header[:8] != b'EFI PART':
            return None
        lba, _, size = struct.unpack_from('<QII', header, 72)
        stream.seek(lba * sector + (number - 1) * size + 16)
        guid = stream.read(16)
    return None if guid == bytes(16) else str(uuid.UUID(bytes_le=guid))


def partition(partuuid, sysfs=None, dev=Path('/dev')):
    """The partition device with PARTUUID: from its uevent where the kernel reports it, else its disk's GPT."""
    sysfs = sysfs or SYSFS
    for uevent in sysfs.glob('*/uevent'):
        fields = dict(line.split('=', 1) for line in uevent.read_text().splitlines() if '=' in line)
        if fields.get('PARTUUID', '').lower() == partuuid.lower():
            return str(dev / fields['DEVNAME'])
    for number in sorted(sysfs.glob('*/partition')):
        part = number.parent
        disk = part.resolve().parent.name
        sector = int((sysfs / disk / 'queue/logical_block_size').read_text())
        if gpt_partuuid(dev / disk, sector, int(number.read_text())) == partuuid.lower():
            return str(dev / part.name)
    raise ValueError(f'No partition {partuuid}')


def entry_for(boot, root, release):
    """The boot entry for ROOT; it must boot the running kernel RELEASE."""
    for path in sorted((boot / 'loader/entries').glob('*.conf')):
        keys = {}
        for line in path.read_text().splitlines():
            key, _, value = line.strip().partition(' ')
            keys.setdefault(key, value.strip())
        if f'root=PARTUUID={root}' not in keys.get('options', '').split():
            continue
        if keys.get('version') != release:
            raise ValueError(f'{path.name} boots kernel {keys.get("version")}, but this recovery runs {release}; '
                             'start it with: orchard launch')
        return path.name
    raise ValueError('The boot partition has no entry for this root')


def main():
    request = json.loads(REQUEST.read_text())
    root, boot = partition(request['root']), partition(request['boot'])
    BOOT.mkdir(parents=True, exist_ok=True)
    run('mount', '-t', 'vfat', '-o', 'ro', boot, str(BOOT))
    try:
        entry = entry_for(BOOT, request['root'], os.uname().release)
    finally:
        run('umount', str(BOOT))
    # The storage fence left every partition read-only; the system mounts
    # these two read-write (its fstab), the rest stays fenced.
    for node in (root, boot):
        run('blockdev', '--setrw', node)
    NEWROOT.mkdir(exist_ok=True)
    run('mount', '-t', 'ext4', '-o', 'rw', root, str(NEWROOT))
    if not os.path.lexists(NEWROOT / 'sbin/init'):
        run('umount', str(NEWROOT))
        raise ValueError('The root has no /sbin/init')
    for name in ('dev', 'proc', 'sys', 'run'):
        (NEWROOT / name).mkdir(exist_ok=True)
    print(json.dumps({'entry': entry, 'root': root}))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(f'start: {error}', file=sys.stderr)
        sys.exit(1)

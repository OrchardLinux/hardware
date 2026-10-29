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
import subprocess
import sys

REQUEST = Path('/run/orchard/start.json')
NEWROOT = Path('/newroot')
BOOT = Path('/run/orchard/start-boot')
SYSFS = Path('/sys/class/block')


def run(*argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'{argv[0]}: {result.stderr.strip()[-500:]}')


def partition(partuuid):
    for uevent in SYSFS.glob('*/uevent'):
        fields = dict(line.split('=', 1) for line in uevent.read_text().splitlines() if '=' in line)
        if fields.get('PARTUUID', '').lower() == partuuid.lower():
            return '/dev/' + fields['DEVNAME']
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

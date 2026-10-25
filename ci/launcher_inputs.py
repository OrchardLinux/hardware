#!/usr/bin/env python3
"""Translate a verified public component bundle into launcher build inputs."""
import argparse
import json
from pathlib import Path, PurePosixPath
from orchard_hardware.image.integrity import sha


def prepare(root, output):
    inventory = json.loads((root / 'components.json').read_text())
    if inventory.get('schema') != 1:
        raise ValueError('Unsupported component inventory')
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != set(inventory['files']) | {'components.json'}:
        raise ValueError('Component inventory membership differs')
    if any(p.is_symlink() for p in root.rglob('*')):
        raise ValueError('Linked component refused')
    for name, expected in inventory['files'].items():
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Unsafe component path')
        source = root / name
        if source.stat().st_size != expected['bytes'] or sha(source) != expected['sha256']:
            raise ValueError('Component differs: ' + name)
    roles = {'loader': 'quiet/m1n1.bin', 'pongo': 'quiet/pongo.bin',
             'kernel': 'kernel/Image.gz', 'dtb': 'kernel/j71b.dtb',
             'kernel_release': 'kernel/kernel.release', 'busybox': 'boot/busybox',
             'dropbear': 'boot/dropbear', 'dropbearkey': 'boot/dropbearkey'}
    files = {role: {'path': str((root / name).resolve()), **inventory['files'][name]}
             for role, name in roles.items()}
    modules = root / 'kernel/lib/modules'
    record = {'files': files, 'modules': {'path': str(modules.resolve()),
              'files': {p.relative_to(modules).as_posix(): sha(p) for p in modules.rglob('*') if p.is_file()}}}
    with output.open('x') as stream:
        json.dump(record, stream, indent=2)
        stream.write('\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--components', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.components, args.output)

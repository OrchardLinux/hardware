#!/usr/bin/env python3
"""Package public hardware outputs and bind quiet assets to their build patches."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def receipt(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected regular component: ' + str(path))
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return {'bytes': path.stat().st_size, 'sha256': digest.hexdigest()}


def package(directory, output):
    if output.exists():
        raise FileExistsError(output)
    here = Path(__file__).resolve().parents[1]
    for name in ('kernel/Image.gz', 'kernel/j71b.dtb', 'kernel/kernel.release',
                 'boot/busybox', 'boot/dropbear', 'boot/dropbearkey',
                 'quiet/m1n1.bin', 'quiet/pongo.bin'):
        receipt(directory / name)
    if not any((directory / 'kernel/lib/modules').rglob('*.ko')):
        raise ValueError('Kernel modules are missing')
    quiet = {'schema': 1, 'profile': 'j71b', 'presentation': 'black',
             'files': {role: receipt(directory / 'quiet' / name)
                       for role, name in (('loader', 'm1n1.bin'), ('pongo', 'pongo.bin'))},
             'patches': {name: receipt(here / 'boot/quiet' / name)['sha256']
                         for name in ('pongo.patch', 'm1n1.patch')}}
    (directory / 'quiet/quiet-boot.json').write_text(json.dumps(quiet, indent=2) + '\n')
    files = {}
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linked component refused')
        if path.is_file() and path.name != 'components.json':
            files[path.relative_to(directory).as_posix()] = receipt(path)
    (directory / 'components.json').write_text(json.dumps({'schema': 1, 'files': files}, indent=2) + '\n')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'x:gz') as archive:
        archive.add(directory, arcname='components')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    package(args.directory, args.output)

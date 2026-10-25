#!/usr/bin/env python3
"""Build unbranded shared-launch boot components, without accessing a device."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Choose a new output directory')
    args.output.mkdir(parents=True)
    here = Path(__file__).resolve().parent
    resume = ['--resume'] if args.resume else []
    subprocess.run([sys.executable, str(here / 'build-pongo.py'),
                    '--output', str(args.output / 'pongo.bin'),
                    '--work-dir', str(args.work_dir / 'pongo'), *resume], check=True)
    subprocess.run(['bash', str(here.parent / 'm1n1/build'), 'j71b',
                    '--output', str(args.output / 'm1n1.bin'),
                    '--work-dir', str(args.work_dir / 'm1n1'),
                    '--extra-patch', str(here / 'm1n1.patch'),
                    '--extra-cflags', '-DQUIET_BOOT', *resume], check=True)
    files = {}
    for role, name in (('loader', 'm1n1.bin'), ('pongo', 'pongo.bin')):
        path = args.output / name
        files[role] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'bytes': path.stat().st_size}
    (args.output / 'quiet-boot.json').write_text(json.dumps({
        'schema': 1, 'profile': 'j71b', 'presentation': 'black', 'files': files,
        'patches': {name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                    for name in ('pongo.patch', 'm1n1.patch')},
    }, indent=2) + '\n')


if __name__ == '__main__':
    main()

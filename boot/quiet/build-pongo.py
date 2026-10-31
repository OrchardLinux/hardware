#!/usr/bin/env python3
"""Build Orchard's one Pongo: black screen, used by every distro and launch; no device access."""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
URL = 'https://github.com/checkra1n/PongoOS.git'
# Must include ad8b2d1 ("Fix for Xcode 16.3: don't depend on section order"): with
# Xcode 16.3+ older revisions copy only part of the image to SRAM and never reach USB.
COMMIT = 'ad8b2d158588e929ae1ce7d9fd8d453720e1a8b6'
NEWLIB = 'f9ea5054de8fb51dff6f6d3c2e7cdd4aa89744b8'


def run(*args):
    subprocess.run(args, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--work-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.is_symlink():
        parser.error('Choose a new output path')
    source = args.work_dir / 'source'
    patch = HERE / 'pongo.patch'
    if args.resume:
        head = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
        if head != COMMIT:
            raise ValueError('Wrong Pongo revision')
        run('git', '-C', str(source), 'apply', '--reverse', '--check', str(patch))
    else:
        if source.exists():
            parser.error('Source exists; use --resume after checking its identity')
        source.mkdir(parents=True)
        run('git', '-C', str(source), 'init', '-q')
        run('git', '-C', str(source), 'remote', 'add', 'origin', URL)
        run('git', '-C', str(source), 'fetch', '--depth', '1', 'origin', COMMIT)
        run('git', '-C', str(source), 'checkout', '-q', '--detach', 'FETCH_HEAD')
        run('git', '-C', str(source), 'apply', '--check', str(patch))
        run('git', '-C', str(source), 'apply', str(patch))
    run('git', '-C', str(source), 'submodule', 'update', '--init', '--recursive', '--depth', '1')
    if subprocess.check_output(['git', '-C', str(source / 'newlib'), 'rev-parse', 'HEAD'], text=True).strip() != NEWLIB:
        raise ValueError('Wrong pinned newlib revision')
    run('make', '-C', str(source), '-j4', 'PONGO_CFLAGS=-DQUIET_BOOT', 'build/Pongo.bin')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as target, (source / 'build/Pongo.bin').open('rb') as binary:
        shutil.copyfileobj(binary, target)
    print('Quiet Pongo:', args.output)


if __name__ == '__main__':main()

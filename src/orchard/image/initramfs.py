#!/usr/bin/env python3
"""Assemble a newc/gzip initramfs from source/runtime and local overlay trees."""
import argparse
import gzip
import os
from pathlib import Path
import stat


def entries(roots):
    selected = {}
    for root in roots:
        if not root.is_dir():
            raise ValueError(f'not a directory: {root}')
        for path in sorted(root.rglob('*')):
            name = path.relative_to(root).as_posix()
            if path.is_symlink():
                mode, data = stat.S_IFLNK | 0o777, os.readlink(path).encode()
            elif path.is_dir():
                mode, data = stat.S_IFDIR | (path.stat().st_mode & 0o777), b''
            elif path.is_file():
                mode, data = stat.S_IFREG | (path.stat().st_mode & 0o777), path.read_bytes()
            else:
                raise ValueError(f'unsupported initramfs entry: {path}')
            if name in selected and selected[name] != (mode, data):
                raise ValueError(f'conflicting initramfs input: {name}')
            selected[name] = mode, data
    return selected


def record(out, inode, name, mode, data):
    label = name.encode() + b'\0'
    fields = (inode, mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(label), 0)
    out.write(b'070701' + b''.join(f'{number:08x}'.encode() for number in fields))
    out.write(label)
    out.write(b'\0' * (-(110 + len(label)) % 4))
    out.write(data)
    out.write(b'\0' * (-len(data) % 4))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True, help='prepared initramfs runtime tree')
    parser.add_argument('--overlay', type=Path, action='append', default=[], help='local tree to include; repeat as needed')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists; choose a new artifact name')
    files = entries([args.root, *args.overlay])
    if 'init' not in files or not stat.S_ISREG(files['init'][0]) or not files['init'][0] & 0o111:
        parser.error('runtime must provide an executable /init')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as out:
        for index, (name, (mode, data)) in enumerate(sorted(files.items()), 1):
            record(out, index, name, mode, data)
        record(out, len(files) + 1, 'TRAILER!!!', 0, b'')
        out.write(b'\0' * 512)
    print(f'Built {args.output} from {len(files)} entries')


if __name__ == '__main__':
    main()

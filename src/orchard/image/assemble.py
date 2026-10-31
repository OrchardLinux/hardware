#!/usr/bin/env python3
"""Combine loader, boot arguments, DTBs, kernel and initramfs into an m1n1 image.

m1n1 boots with the DTB whose root compatible names the device it runs on, so
one image carries the DTB of every device it supports.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path


INITRAMFS_MAGIC = b'm1n1_initramfs'


def initramfs_header(path):
    size = path.stat().st_size
    if not 0 < size <= 0xffffffff:
        raise ValueError('Initramfs size exceeds m1n1 framing limits')
    # m1n1 passes this payload directly to Linux instead of expanding gzip in
    # its tiny loader inflater. Linux performs the normal initramfs unpack.
    return INITRAMFS_MAGIC + struct.pack('<I', size)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True, help='device boot profile')
    for name in ('loader', 'kernel', 'initramfs', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--dtb', type=Path, action='append', required=True, help='repeat once per device')
    args = parser.parse_args()
    bootargs = json.loads(args.manifest.read_text())['bootargs'].encode()
    if not bootargs.startswith(b'chosen.bootargs=') or not bootargs.endswith(b'\n'):
        parser.error('invalid device boot arguments')
    if args.output.exists():
        parser.error('output already exists; choose a new artifact name')
    inputs = [('loader', args.loader), ('bootargs', bootargs), *(('dtb', dtb) for dtb in args.dtb),
              ('kernel', args.kernel), ('initramfs', args.initramfs)]
    for name, source in inputs:
        if not isinstance(source, bytes) and not source.is_file():
            parser.error(f'missing {name}: {source}')
    inputs.insert(-1, ('initramfs_header', initramfs_header(args.initramfs)))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    components = []
    offset = 0
    with args.output.open('xb') as result:
        for name, source in inputs:
            digest = hashlib.sha256()
            size = 0
            if isinstance(source, bytes):
                blocks = (source,)
            else:
                def chunks(path):
                    with path.open('rb') as stream:
                        while block := stream.read(1024 * 1024):
                            yield block
                blocks = chunks(source)
            for block in blocks:
                result.write(block)
                digest.update(block)
                size += len(block)
            components.append(dict(name=name, offset=offset, bytes=size, sha256=digest.hexdigest()))
            offset += size
    receipt = args.output.with_suffix(args.output.suffix + '.json')
    receipt.write_text(json.dumps({'image_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
                                   'initramfs_encoding': 'm1n1_initramfs',
                                   'components': components}, indent=2) + '\n')
    print(f'Built {args.output}; component receipt: {receipt}')


if __name__ == '__main__':
    main()

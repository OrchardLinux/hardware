#!/usr/bin/env python3
"""Extract the VXD (DEVA) video-decoder firmware from a legacy prelinked kernelcache.

AppleD5500 has no __firmware section. Its __DATA.__data holds a record

    +0x00  char *name       -> __TEXT.__cstring ("REL_812.0_14_Deva")
    +0x08  char *build      -> __TEXT.__cstring (build date)
    +0x10  u32   mtx_size   (memory the decoder core needs; <= image size)
    +0x14  u32   size       (image size)
    +0x18  u8   *image      -> __TEXT.__const

The record is found by that pointer pattern, not by fixed offsets, and the
image is copied unchanged. The caller pins its SHA-256.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Dict, Tuple

from orchard_cli.apple.prelink import Kernelcache, named

D5500 = 'com.apple.driver.AppleD5500'
DATA, CSTRING, CONST = ('__DATA', '__data'), ('__TEXT', '__cstring'), ('__TEXT', '__const')
RECORD = struct.Struct('<QQIIQ')


def extract(blob: bytes) -> Tuple[bytes, Dict[str, Any]]:
    kernel = Kernelcache(blob)
    sections = named(kernel.kext_sections(D5500, 'AppleD5500'), DATA, CSTRING, CONST)

    def inside(addr: int, key: Tuple[str, str], size: int = 1) -> bool:
        s = sections[key]
        return bool(s['addr'] <= addr and addr + size <= s['addr'] + s['size'])

    def cstring(addr: int) -> str:
        s = sections[CSTRING]
        text = kernel.read(addr, min(64, s['addr'] + s['size'] - addr)).split(b'\0')[0]
        value = text.decode('ascii', 'replace')
        return value if value.isprintable() and '�' not in value else ''

    data = kernel.read(sections[DATA]['addr'], sections[DATA]['size'])
    records = []
    for offset in range(0, len(data) - RECORD.size + 1, 8):
        name, build, mtx_size, size, image = RECORD.unpack_from(data, offset)
        if (inside(name, CSTRING) and inside(build, CSTRING) and 4096 < size <= 4 * 1024 * 1024
                and mtx_size <= size and inside(image, CONST, size) and cstring(name).endswith('_Deva')):
            records.append((cstring(name), cstring(build), image, size))
    if len(records) != 1:
        raise ValueError('No unique VXD firmware record')
    name, build, image, size = records[0]
    firmware = kernel.read(image, size)
    report = dict(kext=D5500, firmware=name, build=build, kernel_file_offset=kernel.offset(image, size),
                  firmware_size=size, sha256=hashlib.sha256(firmware).hexdigest())
    return firmware, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    data, report = extract(args.kernel.read_bytes())
    # Never silently replace a firmware artifact.
    with args.output.open('xb') as out:
        out.write(data)
    print(json.dumps(report, indent=2))

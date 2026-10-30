#!/usr/bin/env python3
"""Extract SmartIO __DATA.__firmware from a legacy prelinked ARM64 kernelcache.

No hardcoded offsets; select the exact kext via its prelink metadata, then map
its section virtual address through the outer kernel's file-backed segments.
For MH_FILESET kernels use the upstream ipsw kext extractor instead. The kext
bundle identifier is the caller's board policy (T8010 by default; S8000 for A9).
"""
import argparse
import hashlib
import json
from pathlib import Path

from orchard.apple.prelink import Kernelcache

T8010 = 'com.apple.driver.AppleT8010SmartIO'
S8000 = 'com.apple.driver.AppleS8000SmartIO'
BUNDLES = (T8010, S8000)


def extract(blob, bundle=T8010):
    kernel = Kernelcache(blob)
    ksections = kernel.kext_sections(bundle, 'SmartIO')
    firmware = [s for s in ksections if (s['segment'], s['name']) == ('__DATA', '__firmware')]
    if len(firmware) != 1 or not 4096 <= firmware[0]['size'] <= 4*1024*1024:
        raise ValueError('No unique bounded firmware section')
    f = firmware[0]; offset = kernel.offset(f['addr'], f['size'])
    data = blob[offset:offset+f['size']]
    report = dict(kext=bundle, kernel_file_offset=offset,
                  firmware_size=len(data), sha256=hashlib.sha256(data).hexdigest())
    return data, report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kernel', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--bundle', choices=BUNDLES, default=T8010)
    args = parser.parse_args()
    data, report = extract(args.kernel.read_bytes(), args.bundle)
    # Never silently replace a firmware artifact.
    with args.output.open('xb') as out:
        out.write(data)
    print(json.dumps(report, indent=2))

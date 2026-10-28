#!/usr/bin/env python3
"""Kernel platform layers: ordered patches, and device -> platform/DTB resolution.

  series.py PLATFORM_OR_DEVICE            print the ordered patch files
  series.py --resolve PLATFORM_OR_DEVICE  print "platform dtb" (dtb is "-" for a platform)
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent
DEVICES = ROOT.parent / 'devices'


def resolve(name):
    """A device names its platform and DTB in device.toml [kernel]; a platform names itself."""
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', name):
        raise ValueError('Invalid device or platform name')
    descriptor = DEVICES / name / 'device.toml'
    if descriptor.is_file():
        section = re.search(r'^\[kernel\]\n(.*?)(?=^\[|\Z)', descriptor.read_text(), re.M | re.S)
        values = dict(re.findall(r'^(platform|dtb)\s*=\s*"([a-z0-9][a-z0-9_.-]*)"', section[1], re.M)) if section else {}
        if set(values) != {'platform', 'dtb'}:
            raise ValueError(f'{descriptor}: [kernel] needs platform and dtb')
        return values['platform'], values['dtb']
    if (ROOT / 'series' / name).is_file():
        return name, None
    raise ValueError(f'Unknown device or platform: {name}')


def patches(name):
    series = ROOT / 'series' / resolve(name)[0]
    result = []
    for line in series.read_text().splitlines():
        line = line.split('#', 1)[0].strip()
        if not line:
            continue
        layer = (ROOT / line).resolve()
        if ROOT not in layer.parents or not layer.is_dir():
            raise ValueError(f'Invalid patch layer in {series}: {line}')
        result += sorted(layer.rglob('*.patch'))
    return result


if __name__ == '__main__':
    if sys.argv[1] == '--resolve':
        platform, dtb = resolve(sys.argv[2])
        print(platform, dtb or '-')
    else:
        for patch in patches(sys.argv[1]):
            print(patch)

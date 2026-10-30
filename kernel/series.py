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
    platform = resolve(name)[0]
    if producer(name) != 'linux-arm64':
        raise ValueError(f'{platform} patches are managed by its own producer')
    series = ROOT / 'series' / platform
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


def producer(name):
    """Resolve a platform producer within the packaged kernel resources."""
    platform, device_dtb = resolve(name)
    metadata = ROOT / 'platforms' / (platform + '.toml')
    if metadata.is_symlink():
        raise ValueError(f'Invalid platform metadata: {metadata}')
    if not metadata.exists():
        return 'linux-arm64'
    if not metadata.is_file():
        raise ValueError(f'Invalid platform metadata: {metadata}')
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib
    value = tomllib.loads(metadata.read_text())
    selected = value.get('producer')
    if (value.get('schema') != 1 or not isinstance(selected, str) or
            not re.fullmatch(r'[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*)+', selected)):
        raise ValueError(f'Unsupported platform producer in {metadata}')
    path = ROOT.joinpath(*selected.split('/'))
    if (not path.is_file() or path.is_symlink() or ROOT.resolve() not in path.resolve().parents or
            any(parent.is_symlink() for parent in path.parents if parent != ROOT and ROOT in parent.parents)):
        raise ValueError(f'Invalid platform producer path in {metadata}')
    artifact_dtb = value.get('dtb')
    if device_dtb and artifact_dtb is not None and artifact_dtb != device_dtb + '.dtb':
        raise ValueError(f'Platform device tree differs from {metadata}')
    return selected


if __name__ == '__main__':
    if sys.argv[1] == '--resolve':
        platform, dtb = resolve(sys.argv[2])
        print(platform, dtb or '-')
    elif sys.argv[1] == '--producer':
        print(producer(sys.argv[2]))
    else:
        for patch in patches(sys.argv[1]):
            print(patch)

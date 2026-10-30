"""Minimal hardware checkout in the platform-series layout, for kernel cache tests."""
from pathlib import Path
import shutil

SERIES = '''#!/usr/bin/env python3
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent


def resolve(name):
    descriptor = ROOT.parent / 'devices' / name / 'device.toml'
    if descriptor.is_file():
        values = dict(re.findall(r'^(platform|dtb)\\s*=\\s*"([^"]+)"', descriptor.read_text(), re.M))
        return values['platform'], values['dtb']
    return name, None


def patches(name):
    result = []
    for line in (ROOT / 'series' / resolve(name)[0]).read_text().splitlines():
        if line.strip():
            result += sorted((ROOT / line.strip()).rglob('*.patch'))
    return result


if sys.argv[1] == '--resolve':
    platform, dtb = resolve(sys.argv[2])
    print(platform, dtb or '-')
else:
    for patch in patches(sys.argv[1]):
        print(patch)
'''


def write(hardware, device='j71b', platform='apple', dtb='t8010-j71b'):
    """Series, platform config and device descriptor; returns the patch layer."""
    hardware = Path(hardware)
    files = {
        'kernel/series.py': SERIES,
        'kernel/series/' + platform: 'patches/' + platform + '\n',
        'kernel/configs/' + platform + '/config': 'config',
        'devices/' + device + '/device.toml': f'[kernel]\nplatform = "{platform}"\ndtb = "{dtb}"\n',
    }
    for name, text in files.items():
        path = hardware / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    layer = hardware / 'kernel/patches' / platform
    layer.mkdir(parents=True, exist_ok=True)
    return layer

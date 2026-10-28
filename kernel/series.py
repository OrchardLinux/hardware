#!/usr/bin/env python3
"""Print the ordered kernel patch files for a device (kernel/series/DEVICE)."""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent


def patches(device):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', device):
        raise ValueError('Invalid device name')
    series = ROOT / 'series' / device
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
    for patch in patches(sys.argv[1]):
        print(patch)

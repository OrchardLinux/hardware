#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Render owner-supplied, already-framed J72 packets as private C includes.

No firmware extraction, packet construction, device access or authenticity
validation occurs here. Obtain and validate matching inputs separately.
"""
import argparse
import os
from pathlib import Path

PACKETS = {
    'firmware': ('j72_fw_packet.inc', 57016),
    'proximity': ('j72_prox_packet.inc', 144),
    'panel_first': ('j72_cal_packet_0.inc', 1024),
    'panel_last': ('j72_cal_packet_1.inc', 32),
}


def render(inputs, output):
    packets = {}
    for key, (name, size) in PACKETS.items():
        data = Path(inputs[key]).read_bytes()
        if len(data) != size:
            raise ValueError(f'{key}: expected {size} already-framed bytes, got {len(data)}')
        packets[name] = data
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    if output.is_symlink():
        raise ValueError('output directory must not be a symbolic link')
    if any((output / name).exists() or (output / name).is_symlink() for name in packets):
        raise FileExistsError('refusing to overwrite existing generated inputs')
    os.chmod(output, 0o700)
    for name, data in packets.items():
        fd = os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            for offset in range(0, len(data), 16):
                f.write(', '.join(f'0x{value:02x}' for value in data[offset:offset + 16]) + ',\n')
    return list(packets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in PACKETS:
        parser.add_argument('--' + key.replace('_', '-'), required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        names = render(vars(args), args.output)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    print('Created four private include files; keep them and compiled modules local.')
    for name in names:
        print(name)


if __name__ == '__main__':
    main()

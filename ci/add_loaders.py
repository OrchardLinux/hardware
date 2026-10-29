#!/usr/bin/env python3
"""Add the launch loader (Pongo, m1n1) to the one hardware wheel the workflow built.

    add_loaders.py WHEEL M1N1 PONGO

orchard launch needs only these two (orchard_hardware.loader). They are
built after the wheel, by other jobs, so they are added to it here instead
of building the wheel again: the files go to orchard_hardware/loaders/ and
the wheel's RECORD gets their hashes. The wheel is replaced in place.
"""
import base64
import hashlib
from pathlib import Path
import sys
import zipfile


def record_line(name, data):
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
    return f'{name},sha256={digest},{len(data)}'


def add(wheel, files):
    wheel = Path(wheel)
    temporary = wheel.with_suffix('.part')
    with zipfile.ZipFile(wheel) as source, zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as target:
        (record,) = [n for n in source.namelist() if n.endswith('.dist-info/RECORD')]
        lines = source.read(record).decode().splitlines()
        for name, path in files.items():
            if name in source.namelist():
                raise ValueError('The wheel already has ' + name)
        for info in source.infolist():
            if info.filename != record:
                target.writestr(info, source.read(info.filename))
        for name, path in files.items():
            data = Path(path).read_bytes()
            target.writestr(name, data)
            lines.insert(len(lines) - 1, record_line(name, data))
        target.writestr(record, '\n'.join(lines) + '\n')
    temporary.replace(wheel)


if __name__ == '__main__':
    wheel, m1n1, pongo = sys.argv[1:]
    add(wheel, {'orchard_hardware/loaders/m1n1.bin': m1n1, 'orchard_hardware/loaders/pongo.bin': pongo})

#!/usr/bin/env python3
"""Stage GitHub wheel assets and generate the latest CLI requirements.txt."""
import argparse
from email.parser import BytesParser
import hashlib
from pathlib import Path
import re
import shutil
import zipfile


def wheel_info(path, name):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular wheel: ' + str(path))
    with zipfile.ZipFile(path) as archive:
        metadata = [n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError('Ambiguous wheel metadata')
        info = BytesParser().parsebytes(archive.read(metadata[0]))
    if info['Name'].replace('_', '-') != name or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', info['Version']):
        raise ValueError('Unexpected wheel name/version')
    if path.name != f"{name.replace('-', '_')}-{info['Version']}-py3-none-any.whl":
        raise ValueError('Expected a universal Python wheel')
    return info['Version']


def prepare(cli_wheel, hardware_wheel, output):
    entries = [('cli', 'orchard-cli', cli_wheel), ('hardware', 'orchard-hardware', hardware_wheel)]
    rows = []
    for repo, package, wheel in entries:
        version = wheel_info(wheel, package)
        checksum = hashlib.sha256(wheel.read_bytes()).hexdigest()
        rows.append((repo, wheel, f'{package} @ https://github.com/OrchardLinux/{repo}/releases/download/'
                     f'v{version}/{wheel.name}#sha256={checksum}\n'))
    output.mkdir(parents=True, exist_ok=False)
    for repo, wheel, _ in rows:
        (output / repo).mkdir()
        shutil.copyfile(wheel, output / repo / wheel.name)
    requirements = output / 'cli/requirements.txt'
    requirements.write_text('# Generated from the tested release wheels; no project packages on PyPI.\n' +
                            ''.join(row for _, _, row in rows))
    return requirements


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cli-wheel', type=Path, required=True)
    parser.add_argument('--hardware-wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.cli_wheel, args.hardware_wheel, args.output))


if __name__ == '__main__':
    main()

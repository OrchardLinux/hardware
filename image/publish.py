#!/usr/bin/env python3
"""Stage local release artifacts as a GitHub-hosted release, with immutable URLs."""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from orchard_hardware.image.integrity import sha
try:
    import tomllib
except ImportError:
    import tomli as tomllib


def toml(document, prefix=()):
    lines = []
    if prefix:
        lines.append('[' + '.'.join(json.dumps(key) for key in prefix) + ']')
    for key, value in document.items():
        if not isinstance(value, dict):
            lines.append(json.dumps(key) + ' = ' + json.dumps(value))
    for key, value in document.items():
        if isinstance(value, dict):
            lines.extend(['', toml(value, prefix + (key,))])
    return '\n'.join(lines) + '\n'


def local_asset(directory, receipt):
    name = receipt.get('file', '')
    if not name or Path(name).name != name or name in ('.', '..') or '\\' in name:
        raise ValueError('Publication requires local sibling artifact files')
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing/linked artifact: ' + name)
    if path.stat().st_size != receipt['bytes'] or sha(path) != receipt['sha256']:
        raise ValueError('Artifact differs from receipt: ' + name)
    return path


def publish(directory, tag, output, *, distro):
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', distro):
        raise ValueError('Invalid distro repository name')
    if tag in ('latest', 'main', 'master') or not re.fullmatch(r'v?[A-Za-z0-9][A-Za-z0-9._-]*', tag):
        raise ValueError('Invalid release tag')
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    index_path = directory / 'index.toml'
    if index_path.is_symlink():
        raise ValueError('Linked index refused')
    index = tomllib.loads(index_path.read_text())
    if index.get('kind') != 'orchard.index' or index.get('distro') != distro:
        raise ValueError('Expected a matching distro release index')
    metadata_names = {'index.toml'} | {pin.get('file') for methods in index['devices'].values() for pin in methods.values()}
    base = 'https://github.com/OrchardLinux/' + distro + '/releases/download/' + tag
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.publish-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'release'
        stage.mkdir()
        hosted = copy.deepcopy(index)
        for profile, methods in index['devices'].items():
            for method, pin in methods.items():
                source = local_asset(directory, pin)
                manifest = tomllib.loads(source.read_text())
                if (manifest.get('kind') != 'orchard.install' or manifest.get('distro') != distro
                        or manifest.get('device') != profile or manifest.get('boot_method') != method):
                    raise ValueError('Manifest does not match its index entry')
                for role in ('root', 'boot', 'pongo'):
                    receipt = manifest[role]
                    artifact = local_asset(directory, receipt)
                    if artifact.name in metadata_names:
                        raise ValueError('Artifact name collides with release metadata')
                    target = stage / artifact.name
                    if target.exists():
                        if sha(target) != receipt['sha256']:
                            raise ValueError('Conflicting release artifact names')
                    else:
                        try:
                            os.link(artifact, target)
                        except OSError:
                            shutil.copyfile(artifact, target)
                    receipt['url'] = base + '/' + receipt.pop('file')
                target = stage / source.name
                with target.open('x') as stream:
                    stream.write(toml(manifest))
                hosted['devices'][profile][method] = {
                    'url': base + '/' + source.name, 'bytes': target.stat().st_size, 'sha256': sha(target)}
        (stage / 'index.toml').write_text(toml(hosted))
        stage.rename(output)
    return output / 'index.toml'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-dir', type=Path, required=True)
    parser.add_argument('--tag', required=True, help='immutable GitHub release tag, not latest')
    parser.add_argument('--distro', required=True, choices=['omarchy-a', 'minimal-linux'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(publish(args.release_dir, args.tag, args.output, distro=args.distro))


if __name__ == '__main__':
    main()

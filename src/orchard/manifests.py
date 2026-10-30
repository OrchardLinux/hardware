#!/usr/bin/env python3
"""Offline checks for Orchard indexes and resolved manifests; not an installer."""
import argparse
import json
from pathlib import Path
import sys
import tomllib
from .http import validate_url
from .integrity import digest

from jsonschema import Draft202012Validator

from .hardware import ROOT

SCHEMA_DIR = ROOT / 'schemas/v1'
SCHEMAS = {
    'orchard.index': ROOT / 'schemas/v2/index.schema.json',
    'orchard.install': ROOT / 'schemas/v2/install.schema.json',
    'orchard.boot': SCHEMA_DIR / 'boot.schema.json',
    'hardware.device': SCHEMA_DIR / 'device.schema.json',
}


def firmware_schema():
    """The firmware recipe schema shipped with the installed hardware data."""
    return json.loads((SCHEMA_DIR / 'firmware.schema.json').read_text())


class ManifestError(ValueError):
    pass


def sources(document):
    if document['kind'] == 'orchard.index':
        yield 'release', document['release']
    elif document['kind'] in ('orchard.install', 'orchard.boot'):
        for name in ('root', 'boot', 'pongo'):
            if name in document:
                yield name, document[name]
        for platform, row in document.get('platforms', {}).items():
            for name in ('boot', 'layer'):
                yield f'platforms.{platform}.{name}', row[name]


def check_source(source, where):
    if 'file' in source:
        value = source['file']
        if ('\\' in value or ':' in value or
                any(ord(c) < 32 or ord(c) == 127 for c in value) or
                any(part in ('', '.', '..') for part in value.split('/'))):
            raise ManifestError(f'{where}: expected normalized relative bundle path')
    else:
        try:
            validate_url(source['url'])
        except ValueError as error:
            raise ManifestError(f'{where}: {error}') from error


def validate(document):
    if not isinstance(document, dict):
        raise ManifestError('Expected a TOML table')
    kind = document.get('kind')
    if not isinstance(kind, str) or kind not in SCHEMAS:
        raise ManifestError('Unknown manifest kind: ' + repr(kind))
    schema = json.loads(SCHEMAS[kind].read_text())
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(document),
                    key=lambda error: str(list(error.absolute_path)))
    if errors:
        error = errors[0]
        location = '.'.join(map(str, error.absolute_path)) or '<manifest>'
        raise ManifestError(f'{location}: {error.message}')
    for where, source in sources(document):
        check_source(source, where)
    if kind == 'hardware.device':
        check_source({'file': document['firmware']['recipe']}, 'firmware.recipe')
    return document


def load(path):
    with Path(path).open('rb') as stream:
        return validate(tomllib.load(stream))


def local_file(relative, bundle):
    """Resolve a contained regular local file, refusing symlink components."""
    check_source({'file': relative}, 'file')
    bundle = Path(bundle).absolute()
    if any(parent.is_symlink() for parent in (bundle, *bundle.parents)):
        raise ManifestError('Linked bundle directory refused')
    path = bundle
    for part in relative.split('/'):
        path = path / part
        if path.is_symlink():
            raise ManifestError('Linked asset path refused')
    if not path.is_file():
        raise ManifestError(f'{path}: missing regular file')
    return path


def verify_file(receipt, bundle):
    """Read-only local receipt verification; no downloads or execution."""
    check_source(receipt, 'source')
    if 'file' not in receipt:
        raise ManifestError('URL not downloaded: this validator is local-only')
    path = local_file(receipt['file'], bundle)
    if path.stat().st_size != receipt['bytes']:
        raise ManifestError(f'{path}: missing file or byte count differs')
    if digest(path) != receipt['sha256']:
        raise ManifestError(f'{path}: SHA-256 differs')
    return path


def resolve_local(index, bundle):
    """The index's pinned installation manifest, checked to belong to its distro."""
    validate(index)
    if index['kind'] != 'orchard.index':
        raise ManifestError('Expected distro index')
    path = verify_file(index['release'], bundle)
    manifest = load(path)
    if manifest['kind'] != 'orchard.install' or manifest['distro'] != index['distro']:
        raise ManifestError('Resolved manifest does not match the index distro')
    return path, manifest


def platform_for(manifest, board):
    """The platform of MANIFEST that covers BOARD."""
    matches = [name for name, row in manifest['platforms'].items() if board in row['boards']]
    if len(matches) != 1:
        covered = ', '.join(sorted(b for row in manifest['platforms'].values() for b in row['boards']))
        raise ManifestError(f'This release does not cover {board}' + (f' (it covers {covered})' if covered else '')
                            if not matches else f'{board} is listed under several platforms')
    return matches[0]


def verify_local_receipts(document, bundle):
    validate(document)
    if document['kind'] == 'orchard.index':
        path, manifest = resolve_local(document, bundle)
        verify_local_receipts(manifest, path.parent)
    elif document['kind'] == 'hardware.device':
        path = local_file(document['firmware']['recipe'], bundle)
        recipe = json.loads(path.read_text())
        if not isinstance(recipe, dict) or recipe.get('device') not in document['device']['apple_products']:
            raise ManifestError('Firmware recipe belongs to a different device')
    else:
        for _, receipt in sources(document):
            verify_file(receipt, bundle)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifests', nargs='+', type=Path)
    parser.add_argument('--verify-local', action='store_true', help='also verify local referenced files')
    args = parser.parse_args()
    failed = False
    for path in args.manifests:
        try:
            document = load(path)
            if args.verify_local:
                verify_local_receipts(document, path.parent)
            level = 'schema + local receipts' if args.verify_local else 'schema'
            print(f'OK ({level}): {path}')
        except (OSError, ValueError) as error:
            print(f'FAIL: {path}: {error}', file=sys.stderr)
            failed = True
    return int(failed)


if __name__ == '__main__':
    raise SystemExit(main())

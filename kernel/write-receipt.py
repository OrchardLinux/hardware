#!/usr/bin/env python3
"""Bind a kernel image, its device trees and module tree to build inputs."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
try:
    import tomllib
except ImportError:
    import tomli as tomllib


def digest(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Expected a regular file: ' + str(path))
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    return {'bytes': path.stat().st_size, 'sha256': sha.hexdigest()}


def recipe_snapshot(root, device, platform, patch_list):
    """Describe the exact public hardware inputs used by this kernel build."""
    root = Path(root)
    paths = {
        '__init__.py', 'buildenv.py', 'kernel/Containerfile', 'kernel/install-toolchain',
        'kernel/build', 'kernel/build-modules', 'kernel/stage-modules.py',
        'kernel/write-receipt.py', 'kernel/upstream.lock', 'kernel/sources.toml',
        'kernel/series.py', 'kernel/series/' + platform,
        'kernel/configs/' + platform + '/config',
    }
    descriptor = root / 'devices' / device / 'device.toml'
    if descriptor.is_file():
        paths.add('devices/' + device + '/device.toml')
        firmware_name = tomllib.loads(descriptor.read_text()).get('firmware', {}).get('recipe')
        if firmware_name:
            firmware_path = PurePosixPath(firmware_name)
            if (firmware_path.is_absolute() or '..' in firmware_path.parts or
                    '\\' in firmware_name or not firmware_path.parts):
                raise ValueError('Invalid firmware recipe path')
            paths.add('devices/' + device + '/' + firmware_path.as_posix())
    for profile in {platform, device}:
        module_dir = root / 'kernel/modules' / profile
        if module_dir.exists():
            for path in module_dir.rglob('*'):
                if path.is_symlink():
                    raise ValueError('Linked recipe resource refused: ' + str(path))
                if path.is_file():
                    paths.add(path.relative_to(root).as_posix())
    for line in Path(patch_list).read_text().splitlines():
        blob, separator, relative = line.partition(' ')
        path = PurePosixPath(relative)
        if (not separator or not re.fullmatch(r'[0-9a-f]{40}', blob) or
                path.is_absolute() or '..' in path.parts or not path.parts):
            raise ValueError('Invalid applied patch entry')
        full = root / 'kernel' / Path(*path.parts)
        if full.is_symlink() or not full.is_file():
            raise ValueError('Missing or linked patch: ' + relative)
        contents = full.read_bytes()
        actual = hashlib.sha1(b'blob ' + str(len(contents)).encode() + b'\0' + contents).hexdigest()
        if actual != blob:
            raise ValueError('Patch changed during build: ' + relative)
        paths.add('kernel/' + relative)
    files = {name: digest(root / name) for name in sorted(paths)}
    encoded = json.dumps(files, sort_keys=True, separators=(',', ':')).encode()
    return {'sha256': hashlib.sha256(encoded).hexdigest(), 'files': files}


def write_receipt(directory, device, platform, dtb, source_commit, source_tree,
                  prepared_tree, patch_list, config, recipe_root, recipe_file):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', device):
        raise ValueError('Invalid device or platform name')
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', platform):
        raise ValueError('Invalid platform name')
    if dtb != '-' and not re.fullmatch(r'[a-z0-9][a-z0-9_.-]*', dtb):
        raise ValueError('Invalid DTB name')
    for value in (source_commit, source_tree, prepared_tree):
        if not re.fullmatch(r'[0-9a-f]{40}', value):
            raise ValueError('Invalid source identity')
    directory = Path(directory)
    output = directory / 'receipt.json'
    if output.exists() or output.is_symlink():
        raise ValueError('Receipt already exists')
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Expected a kernel output directory')
    release_path = directory / 'kernel.release'
    digest(release_path)
    release = release_path.read_text().strip()
    if not release.endswith('-orchard-' + platform) or '/' in release:
        raise ValueError('Kernel release does not match the platform')
    module_dir = directory / 'lib/modules' / release
    if not module_dir.is_dir() or module_dir.is_symlink():
        raise ValueError('Missing kernel module tree')
    if not any(module_dir.rglob('*.ko')):
        raise ValueError('Missing kernel modules')
    required = ['Image.gz', 'kernel.release']
    if dtb != '-':
        required += [device + '.dtb', 'dtbs/' + dtb + '.dtb']
        if digest(directory / required[-2]) != digest(directory / required[-1]):
            raise ValueError('Device DTB does not match the selected platform DTB')
    files = {}
    for name in required:
        digest(directory / name)
    captured = json.loads(Path(recipe_file).read_text())
    if captured != recipe_snapshot(recipe_root, device, platform, patch_list):
        raise ValueError('Hardware recipe changed during build')
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('Linked kernel output refused: ' + str(path))
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = digest(path)
        elif not path.is_dir():
            raise ValueError('Unsupported kernel output: ' + str(path))
    receipt = {
        'schema': 1,
        'device': device if dtb != '-' else None,
        'platform': platform,
        'dtb': dtb if dtb != '-' else None,
        'kernel_release': release,
        'source': {
            'commit': source_commit,
            'tree': source_tree,
            'prepared_tree': prepared_tree,
            'patch_list_sha256': digest(Path(patch_list))['sha256'],
            'config_sha256': digest(Path(config))['sha256'],
        },
        'hardware_recipe': captured,
        'files': files,
    }
    with output.open('x') as stream:
        json.dump(receipt, stream, indent=2, sort_keys=True)
        stream.write('\n')
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capture', action='store_true')
    parser.add_argument('--directory', type=Path)
    parser.add_argument('--device', required=True)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--dtb')
    parser.add_argument('--source-commit')
    parser.add_argument('--source-tree')
    parser.add_argument('--prepared-tree')
    parser.add_argument('--patch-list', type=Path, required=True)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--recipe-root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--recipe-file', type=Path, required=True)
    args = parser.parse_args()
    if args.capture:
        captured = recipe_snapshot(args.recipe_root, args.device, args.platform, args.patch_list)
        args.recipe_file.write_text(json.dumps(captured, indent=2, sort_keys=True) + '\n')
        return
    if not all((args.directory, args.dtb, args.source_commit, args.source_tree,
                args.prepared_tree, args.config)):
        parser.error('output, device tree, source and configuration are required')
    write_receipt(args.directory, args.device, args.platform, args.dtb,
                  args.source_commit, args.source_tree, args.prepared_tree,
                  args.patch_list, args.config, args.recipe_root, args.recipe_file)


if __name__ == '__main__':
    main()

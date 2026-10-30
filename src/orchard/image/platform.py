"""The platform layer: what orchard puts into every installed system of one platform.

`usr/lib/modules/RELEASE/` holds the kernel (`vmlinuz`), the DTB of every
board of the platform (`dtb/`) and the modules. The regulatory database and
the hardware defaults of `system/defaults` complete it, and
`usr/lib/orchard/platform.json` says what it is. The installer writes the
layer into a root after the distro's root image, then runs kernel-install
(docs/boot-contract.md). Root images carry no kernel, so one serves every
platform, and a kernel change needs a new layer, not a new root image.

Members are regular files under `usr/` only, owned by root, with fixed
times, so the same inputs give the same archive.
"""
import argparse
import gzip
import io
import json
from pathlib import Path
import tarfile

from ..hardware import ROOT
from .integrity import sha
from .runtime import checksums, module_files, regulatory_files

SCHEMA = 1


def boards(platform, hardware=None):
    """{board: dtb} for every board of PLATFORM that the hardware describes."""
    from ..ram import boards_by_platform
    known = boards_by_platform(hardware or ROOT)
    if platform not in known:
        raise ValueError(f'Unknown platform {platform}; known: {", ".join(sorted(known))}')
    return known[platform]


def platforms(hardware=None):
    """Every platform with at least one described board, sorted."""
    from ..ram import boards_by_platform
    return sorted(boards_by_platform(hardware or ROOT))


def kernel(platform, *, hardware=None, cache=None):
    """The staged kernel bundle of PLATFORM, built or reused from the cache."""
    from ..build import prepare_kernel
    return prepare_kernel(hardware, cache, device=sorted(boards(platform, hardware))[0])


def files(bundle, platform, board_dtbs, *, hardware=None):
    """(kernel release, {path: (data, mode)}) of PLATFORM's layer from a staged kernel BUNDLE."""
    hardware = Path(hardware or ROOT)
    bundle = Path(bundle)
    release, result = module_files(bundle / 'lib/modules')
    base = f'usr/lib/modules/{release}/'
    result[base + 'vmlinuz'] = ((bundle / 'Image.gz').read_bytes(), 0o644)
    for dtb in sorted(set(board_dtbs.values())):
        path = bundle / 'dtbs' / (dtb + '.dtb')
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Kernel bundle lacks {path.name}')
        result[base + 'dtb/' + path.name] = (path.read_bytes(), 0o644)
    result.update(regulatory_files())
    defaults = hardware / 'system/defaults'
    for path in sorted(defaults.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'System defaults must not contain links: {path}')
        if path.is_file():
            name = path.relative_to(defaults).as_posix()
            if not name.startswith('usr/lib/'):
                raise ValueError(f'System defaults belong under /usr/lib: {name}')
            result[name] = (path.read_bytes(), 0o644)
    description = {'schema': SCHEMA, 'platform': platform, 'kernel_release': release,
                   'boards': dict(sorted(board_dtbs.items()))}
    result['usr/lib/orchard/platform.json'] = ((json.dumps(description, indent=2) + '\n').encode(), 0o644)
    result['usr/lib/orchard/platform.SHA256SUMS'] = (checksums(result), 0o644)
    return release, result


def write(layer, output):
    """Write LAYER's files to OUTPUT (a new .tar.gz); returns (expanded bytes, members)."""
    expanded = 0
    with Path(output).open('xb') as raw, \
            gzip.GzipFile(fileobj=raw, mode='wb', filename='', mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode='w|', format=tarfile.PAX_FORMAT) as archive:
        for name, (data, mode) in sorted(layer.items()):
            member = tarfile.TarInfo(name)
            member.size, member.mode, member.mtime = len(data), mode, 0
            member.uname = member.gname = 'root'
            archive.addfile(member, io.BytesIO(data))
            expanded += len(data)
    return expanded, len(layer)


def build(platform, output_directory, *, hardware=None, cache=None, bundle=None):
    """PLATFORM's layer as OUTPUT_DIRECTORY/platform-PLATFORM.tar.gz, plus its receipt.

    BUNDLE is a staged kernel bundle; without it the platform's kernel is
    built or reused from the cache.
    """
    board_dtbs = boards(platform, hardware)
    bundle = Path(bundle) if bundle is not None else kernel(platform, hardware=hardware, cache=cache)
    release, layer = files(bundle, platform, board_dtbs, hardware=hardware)
    output = Path(output_directory) / f'platform-{platform}.tar.gz'
    expanded, members = write(layer, output)
    return {'file': output.name, 'bytes': output.stat().st_size, 'sha256': sha(output),
            'compression': 'gzip', 'expanded_bytes': expanded, 'members': members,
            'kernel_release': release, 'boards': sorted(board_dtbs)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('platform', help='e.g. apple-16k')
    parser.add_argument('--output', type=Path, required=True, help='directory for the layer')
    parser.add_argument('--kernel-dir', type=Path, help='a staged kernel bundle (default: build or reuse it)')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(args.platform, args.output, bundle=args.kernel_dir), indent=2))


if __name__ == '__main__':
    main()

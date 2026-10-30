"""The platform layer: what orchard puts into every installed system of one platform.

`usr/lib/modules/RELEASE/` holds the kernel (`vmlinuz`), orchard's initrd
(`initrd`, see boot/initramfs/root), the DTB of every board of the platform
(`dtb/`) and the modules. The regulatory database and the hardware defaults
of `system/defaults` complete it, and `usr/lib/orchard/platform.json` says
what it is. The installer writes the layer into a root after the distro's
root image, then writes the boot entry from it (docs/boot-contract.md). Root
images carry no kernel, so one serves every platform, and a kernel change
needs a new layer, not a new root image.

Members are regular files under `usr/` only, owned by root, with fixed
times, so the same inputs give the same archive.
"""
import argparse
import gzip
import io
import json
from pathlib import Path
import stat
import tarfile
import tempfile

from ..hardware import ROOT
from .integrity import sha
from .runtime import checksums, module_files, regulatory_files

SCHEMA = 1
# What the initrd loads before it looks for the root, in order. Everything
# else storage needs is built in; modprobe adds dependencies from modules.dep.
STORAGE_MODULES = ('nvme',)


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


def tools(hardware=None, cache=None, platform='apple-16k'):
    """orchard's static BusyBox, built or reused from the cache."""
    from ..build import prepare_tools
    return prepare_tools(hardware, cache, device=sorted(boards(platform, hardware))[0]) / 'busybox'


def initrd(modules, release, busybox, *, hardware=None):
    """orchard's initrd for installed systems: BusyBox, the storage modules and their dependencies.

    MODULES is {path: (data, mode)} as module_files gives it.
    """
    hardware = Path(hardware or ROOT)
    base = f'usr/lib/modules/{release}/'
    depends = {}
    for line in modules[base + 'modules.dep'][0].decode().splitlines():
        name, _, needs = line.partition(':')
        depends[name] = needs.split()
    by_name = {Path(name).name.split('.ko')[0].replace('_', '-'): name for name in depends}
    wanted, pending = [], [by_name[name.replace('_', '-')] for name in STORAGE_MODULES]
    while pending:
        name = pending.pop()
        if name not in wanted:
            wanted.append(name)
            pending.extend(depends[name])
    directory = stat.S_IFDIR | 0o755
    entries = {name: (directory, b'') for name in ('bin', 'dev', 'proc', 'sys', 'etc', 'etc/orchard', 'newroot',
                                                    'lib', 'lib/modules', 'lib/modules/' + release)}
    entries['init'] = (stat.S_IFREG | 0o755, (hardware / 'boot/initramfs/root/init').read_bytes())
    entries['bin/busybox'] = (stat.S_IFREG | 0o755, Path(busybox).read_bytes())
    entries['etc/orchard/modules'] = (stat.S_IFREG | 0o644, ('\n'.join(STORAGE_MODULES) + '\n').encode())
    dependency_lines = []
    for name in sorted(wanted):
        target = f'lib/modules/{release}/{name}'
        for parent in reversed(Path(target).parents[:-4]):
            entries.setdefault(parent.as_posix(), (directory, b''))
        entries[target] = (stat.S_IFREG | 0o644, modules[base + name][0])
        dependency_lines.append(f'{name}: {" ".join(depends[name])}'.rstrip(' '))
    entries[f'lib/modules/{release}/modules.dep'] = (stat.S_IFREG | 0o644, ('\n'.join(dependency_lines) + '\n').encode())
    from .initramfs import write as write_initramfs
    with tempfile.TemporaryDirectory() as temporary:
        output = Path(temporary) / 'initrd'
        write_initramfs(entries, output)
        return output.read_bytes()


def files(bundle, platform, board_dtbs, *, busybox, hardware=None):
    """(kernel release, {path: (data, mode)}) of PLATFORM's layer from a staged kernel BUNDLE."""
    hardware = Path(hardware or ROOT)
    bundle = Path(bundle)
    release, result = module_files(bundle / 'lib/modules')
    base = f'usr/lib/modules/{release}/'
    result[base + 'vmlinuz'] = ((bundle / 'Image.gz').read_bytes(), 0o644)
    result[base + 'initrd'] = (initrd(result, release, busybox, hardware=hardware), 0o644)
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


def build(platform, output_directory, *, hardware=None, cache=None, bundle=None, busybox=None):
    """PLATFORM's layer as OUTPUT_DIRECTORY/platform-PLATFORM.tar.gz, plus its receipt.

    BUNDLE is a staged kernel bundle and BUSYBOX orchard's static BusyBox;
    without them they are built or reused from the cache.
    """
    board_dtbs = boards(platform, hardware)
    bundle = Path(bundle) if bundle is not None else kernel(platform, hardware=hardware, cache=cache)
    busybox = Path(busybox) if busybox is not None else tools(hardware, cache, platform)
    release, layer = files(bundle, platform, board_dtbs, busybox=busybox, hardware=hardware)
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
    parser.add_argument('--busybox', type=Path, help="orchard's static BusyBox (default: build or reuse it)")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print(json.dumps(build(args.platform, args.output, bundle=args.kernel_dir, busybox=args.busybox), indent=2))


if __name__ == '__main__':
    main()

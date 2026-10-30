"""RAM-root composition and boot-payload assembly from explicit components.

Device startup belongs to the caller-provided /init. Component build/cache
orchestration lives in build.py; compiler recipes remain hardware-owned.
Device confirmation and upload live in boot.py.
"""
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import sys
import tempfile

from . import build
from .build import command, digest, recipe_key


def check_busybox(path):
    data = path.read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or data[18:20] != b'\xb7\x00':
        raise ValueError('BusyBox must be little-endian AArch64 ELF64')
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    if size < 56 or offset + size * count > len(data):
        raise ValueError('Malformed BusyBox ELF program headers')
    if any(struct.unpack_from('<I', data, offset + i * size)[0] in (2, 3) for i in range(count)):
        raise ValueError('BusyBox must be static (no DYNAMIC or INTERP segment)')


def assemble_ram(hardware, output, *, kernel, busybox, loader, pongo, init,
                 files=None, hostname='orchard-ram', device, modules=False,
                 bootargs='console=tty0 loglevel=7 rdinit=/init', sources=()):
    """Pack a caller-owned /init and arbitrary files. No firmware or modules are
    activated here. Files map relative RAM-root destinations to local files.
    """
    hardware, output = Path(hardware).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    (output / 'build.json').unlink(missing_ok=True)
    kernel, busybox, loader, pongo, init = map(Path, (kernel, busybox, loader, pongo, init))
    check_busybox(busybox)
    if '\n' in bootargs or '\r' in bootargs or not hostname or not all(c.isalnum() or c in '-_' for c in hostname):
        raise ValueError('Invalid boot arguments or hostname')
    release = (kernel / 'kernel.release').read_text().strip()
    if not release or '/' in release or len(release.split()) != 1:
        raise ValueError('Invalid kernel.release')
    inputs = [init, *map(Path, (files or {}).values()), *map(Path, sources)]
    with tempfile.TemporaryDirectory(prefix='build-', dir=output) as temporary:
        work = Path(temporary)
        root = work / 'root'
        for name in ('bin', 'sbin', 'usr/bin', 'usr/sbin', 'dev', 'proc', 'sys', 'run', 'tmp', 'root', 'etc'):
            (root / name).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(busybox, root / 'bin/busybox')
        (root / 'bin/busybox').chmod(0o755)
        shutil.copyfile(init, root / 'init')
        (root / 'init').chmod(0o755)
        (root / 'etc/hostname').write_text(hostname + '\n')
        if modules:
            module_tree = kernel / 'lib/modules' / release
            if not (module_tree / 'modules.dep').is_file():
                raise ValueError('Missing matching staged module tree/modules.dep')
            shutil.copytree(module_tree, root / 'lib/modules' / release, symlinks=True)
        for destination, source in (files or {}).items():
            name = PurePosixPath(destination)
            if name.is_absolute() or '..' in name.parts or not name.parts:
                raise ValueError(f'Invalid RAM-root path: {destination}')
            target = root / name
            if target.exists() or target.is_symlink():
                raise ValueError(f'Conflicting RAM-root path: {destination}')
            if any(parent.is_symlink() for parent in target.parents if parent != root.parent):
                raise ValueError(f'Symlinked RAM-root parent: {destination}')
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(Path(source).stat().st_mode & 0o777)
        command(sys.executable, hardware / 'boot/initramfs/pack.py', '--root', root,
                '--output', work / 'initramfs.cpio.gz')
        profile = work / 'boot.json'
        profile.write_text(json.dumps({'bootargs': 'chosen.bootargs=' + bootargs + '\n'}))
        command(sys.executable, hardware / 'boot/assemble.py', '--manifest', profile,
                '--loader', loader, '--dtb', kernel / (device + '.dtb'), '--kernel', kernel / 'Image.gz',
                '--initramfs', work / 'initramfs.cpio.gz', '--output', work / 'linux.bin')
        if (work / 'linux.bin').stat().st_size > 128 * 1024 * 1024:
            raise ValueError('Payload exceeds the Pongo uploader limit (128 MiB)')
        shutil.copyfile(pongo, work / 'pongo.bin')
        receipt = {'kind': 'orchard.ram-build', 'device': device, 'kernel_release': release,
                   'hardware_tested': False,
                   'files': {name: digest(work / name) for name in ('linux.bin', 'pongo.bin')},
                   'sources': {str(p.resolve()): digest(p) for p in inputs}}
        for name in ('linux.bin', 'linux.bin.json', 'pongo.bin', 'initramfs.cpio.gz'):
            os.replace(work / name, output / name)
        (output / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Built {output / "linux.bin"}. No device was accessed.')
    return output


# Where the hardware's static tools go in a RAM image; others land in usr/bin.
TOOL_DESTINATIONS = {'dropbear': 'usr/sbin', 'wpa_supplicant': 'usr/sbin', 'iw': 'usr/sbin'}


def build_directory(directory, *, hardware=None, cache=None, kernel=None,
                    busybox=None, loader=None, pongo=None, modules=False, device=None,
                    firmware=False):
    """Build any DIRECTORY/init plus optional DIRECTORY/rootfs/; no layout convention.

    Unless orchard.toml says transport = false, the payload also gets the USB
    session helper (network, SSH; see ram_session) for its /init to start.
    """
    directory = Path(directory).expanduser().resolve()
    settings = build.configured(directory) or {}
    device = device if device is not None else settings.get('device')
    if device is None:
        raise ValueError('No device selected: add device = "<board>" to orchard.toml')
    if hardware is None:
        import orchard_hardware
        hardware = Path(orchard_hardware.__file__).resolve().parent
    else:
        hardware = Path(hardware).expanduser().resolve()
    cache = Path(cache).expanduser().resolve() if cache is not None else build.cache_root()
    output = directory / 'out'
    output.mkdir(parents=True, exist_ok=True)
    (output / 'build.json').unlink(missing_ok=True)
    init = directory / 'init'
    if not init.is_file():
        raise ValueError(f'Missing device startup script: {init}')
    overlay = directory / 'rootfs'
    files = {}
    if overlay.is_symlink() or (overlay.exists() and not overlay.is_dir()):
        raise ValueError(f'Expected a regular rootfs directory: {overlay}')
    for path in sorted(overlay.rglob('*')):
        if path.is_symlink():
            raise ValueError(f'Symlinks in rootfs are not supported yet: {path}')
        if path.is_file():
            files[str(path.relative_to(overlay))] = path
    tools = build.prepare_tools(hardware, cache, device=device)
    for tool in sorted(tools.iterdir()):
        if tool.name != 'busybox' and tool.is_file():
            files.setdefault(TOOL_DESTINATIONS.get(tool.name, 'usr/bin') + '/' + tool.name, tool)
    if settings.get('transport', True):
        from . import ram_session
        for destination, source in ram_session.payload_files().items():
            if destination in files:
                raise ValueError(f'rootfs/ provides {destination}; set transport = false to supply your own')
            files[destination] = source
    if firmware:
        from . import firmware as firmwares
        from .progress import Clock, Progress
        recipe = firmwares.recipe_for(device, hardware)
        outputs = firmwares.ensure(device, Clock(), Progress(), hardware=hardware)
        for item in recipe['outputs']:
            if item['destination'] in files:
                raise ValueError(f'rootfs/ and the {device} firmware both provide {item["destination"]}')
            files[item['destination']] = outputs / item['sha256']
    components = build.prepare_components(hardware, cache, device=device, kernel=kernel,
                                          busybox=busybox, loader=loader, pongo=pongo)
    hostname = re.sub(r'[^A-Za-z0-9_-]', '-', directory.name).strip('-_')[:63] or 'orchard-ram'
    assemble_ram(hardware, output, **components, init=init, files=files,
                 modules=modules, hostname=hostname, device=device)
    receipt = json.loads((output / 'build.json').read_text())
    # Catch newly added/removed overlay files too, not only changes to old inputs.
    receipt['overlay'] = {'path': str(overlay), 'sha256': recipe_key(directory, ['rootfs'], ignore=())}
    (output / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return output


def verify_ram(output):
    output = Path(output).resolve()
    receipt = json.loads((output / 'build.json').read_text())
    if (receipt.get('kind') not in (None, 'orchard.ram-build') or not receipt.get('device') or
            set(receipt['files']) != {'linux.bin', 'pongo.bin'}):
        raise ValueError('Not a RAM-boot build')
    for name, expected in receipt['files'].items():
        path = output / name
        if path.is_symlink() or digest(path) != expected:
            raise ValueError(f'{path} changed; rebuild before booting')
    # Old charger receipts remain bootable across this host-side refactor. Their
    # source list included host launchers, which are not part of the RAM payload.
    if 'overlay' in receipt:
        overlay = Path(receipt['overlay']['path'])
        if recipe_key(overlay.parent, [overlay.name], ignore=()) != receipt['overlay']['sha256']:
            raise ValueError(f'{overlay} changed; rebuild before booting')
    for name, expected in receipt.get('sources', {}).items():
        if digest(Path(name)) != expected:
            raise ValueError(f'{name} changed; rebuild before booting')
    return receipt



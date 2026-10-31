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
                 files=None, hostname='orchard-ram', platform, boards, modules=False,
                 bootargs='console=tty0 loglevel=7 rdinit=/init', sources=()):
    """Pack a caller-owned /init and arbitrary files for every board of PLATFORM.

    BOARDS maps each board to its DTB name; m1n1 picks the booted board's DTB.
    No firmware or modules are activated here. Files map relative RAM-root
    destinations to local files.
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
        command(sys.executable, '-m', 'orchard.image.initramfs', '--root', root,
                '--output', work / 'initramfs.cpio.gz')
        profile = work / 'boot.json'
        profile.write_text(json.dumps({'bootargs': 'chosen.bootargs=' + bootargs + '\n'}))
        dtbs = [kernel / 'dtbs' / (dtb + '.dtb') for dtb in boards.values()]
        for path in dtbs:
            if not path.is_file():
                raise ValueError(f'Kernel bundle lacks {path.name}')
        assemble(profile, loader, dtbs, kernel / 'Image.gz', work / 'initramfs.cpio.gz', work / 'linux.bin')
        if (work / 'linux.bin').stat().st_size > 128 * 1024 * 1024:
            raise ValueError('Payload exceeds the Pongo uploader limit (128 MiB)')
        shutil.copyfile(pongo, work / 'pongo.bin')
        receipt = {'kind': 'orchard.ram-build', 'platform': platform, 'boards': sorted(boards),
                   'kernel_release': release, 'hardware_tested': False,
                   # What `orchard run` needs to add a board's firmware layer.
                   'assembly': {'loader': str(loader), 'kernel': str(kernel / 'Image.gz'),
                                'dtbs': [str(path) for path in dtbs], 'bootargs': bootargs},
                   'files': {name: digest(work / name) for name in ('linux.bin', 'pongo.bin')},
                   'sources': {str(p.resolve()): digest(p) for p in inputs}}
        for name in ('linux.bin', 'linux.bin.json', 'pongo.bin', 'initramfs.cpio.gz'):
            os.replace(work / name, output / name)
        (output / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    print(f'Built {output / "linux.bin"}. No device was accessed.')
    return output


def assemble(profile, loader, dtbs, kernel, initramfs, output):
    command(sys.executable, '-m', 'orchard.image.assemble', '--manifest', profile, '--loader', loader,
            *(argument for dtb in dtbs for argument in ('--dtb', dtb)), '--kernel', kernel,
            '--initramfs', initramfs, '--output', output)


# Where the hardware's static tools go in a RAM image; others land in usr/bin.
TOOL_DESTINATIONS = {'dropbear': 'usr/sbin', 'wpa_supplicant': 'usr/sbin', 'iw': 'usr/sbin'}


def boards_by_platform(hardware):
    """{platform: {board: dtb}} for every board the hardware describes."""
    from . import kernel as kernels
    result = {}
    for descriptor in sorted(Path(hardware, 'devices').glob('*/device.toml')):
        try:
            platform, dtb = kernels.layout(hardware, descriptor.parent.name)
        except ValueError:
            continue
        result.setdefault(platform, {})[descriptor.parent.name] = dtb
    return result


def resolve_platform(directory, hardware, *, platform=None, device=None):
    """--platform, else the platform of --device, orchard.local.toml or the attached board."""
    known = boards_by_platform(hardware)
    if platform is not None:
        if platform not in known:
            raise ValueError(f'Unknown platform {platform}; known: {", ".join(sorted(known))}')
        return platform
    board = build.resolve_device(directory, device)
    if board is None:
        from .devices import attached_board
        board = attached_board()
    for name, boards in known.items():
        if board in boards:
            return name
    listing = '; '.join(f'{name}: {", ".join(boards)}' for name, boards in sorted(known.items()))
    if board is not None:
        raise ValueError(f'No hardware support describes {board}. Platforms and their boards: {listing}')
    raise ValueError('Which platform? Pass --device BOARD or --platform PLATFORM, set device in '
                     f'orchard.local.toml, or connect the device. Platforms and their boards: {listing}')


def build_directory(directory, *, hardware=None, cache=None, kernel=None,
                    busybox=None, loader=None, pongo=None, platform=None, device=None):
    """Build DIRECTORY/init plus optional DIRECTORY/rootfs/ for every board of one platform.

    The payload goes to DIRECTORY/out/PLATFORM/. Unless payload.toml says
    transport = false it also gets the USB session helper (network, SSH; see
    ram_session) for its /init to start. Firmware is never part of it: `orchard
    run` adds the booted board's (with_firmware).
    """
    directory = Path(directory).expanduser().resolve()
    settings = build.payload_settings(directory)
    if hardware is None:
        from .hardware import ROOT
        hardware = ROOT
    else:
        hardware = Path(hardware).expanduser().resolve()
    platform = resolve_platform(directory, hardware, platform=platform, device=device)
    boards = boards_by_platform(hardware)[platform]
    # Components are per platform; any of its boards selects them.
    representative = sorted(boards)[0]
    cache = Path(cache).expanduser().resolve() if cache is not None else build.cache_root()
    output = directory / 'out' / platform
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
    tools = build.prepare_tools(hardware, cache, device=representative)
    for tool in sorted(tools.iterdir()):
        if tool.name != 'busybox' and tool.is_file():
            files.setdefault(TOOL_DESTINATIONS.get(tool.name, 'usr/bin') + '/' + tool.name, tool)
    if settings['transport']:
        from . import ram_session
        for destination, source in ram_session.payload_files().items():
            if destination in files:
                raise ValueError(f'rootfs/ provides {destination}; set transport = false in payload.toml to supply your own')
            files[destination] = source
    components = build.prepare_components(hardware, cache, device=representative, kernel=kernel,
                                          busybox=busybox, loader=loader, pongo=pongo)
    hostname = re.sub(r'[^A-Za-z0-9_-]', '-', directory.name).strip('-_')[:63] or 'orchard-ram'
    assemble_ram(hardware, output, **components, init=init, files=files, modules=settings['modules'],
                 hostname=hostname, platform=platform, boards=boards)
    receipt = json.loads((output / 'build.json').read_text())
    # Catch newly added/removed overlay files too, not only changes to old inputs.
    receipt['overlay'] = {'path': str(overlay), 'sha256': recipe_key(directory, ['rootfs'], ignore=())}
    receipt['firmware'] = settings['firmware']
    (output / 'build.json').write_text(json.dumps(receipt, indent=2) + '\n')
    return output


def with_firmware(output, board, *, hardware=None, cache=None):
    """OUTPUT's payload plus BOARD's firmware as a second initramfs, in the private cache.

    Linux unpacks concatenated initramfs archives in order, so the platform
    payload stays as built. Firmware is downloaded once (IPSW byte ranges) and
    never written to the project directory.
    """
    from . import firmware as firmwares
    from .image import initramfs
    from .progress import Clock, Progress
    receipt = verify_ram(output)
    if board not in receipt['boards']:
        raise ValueError(f'This payload is for {receipt["platform"]} ({", ".join(receipt["boards"])}), not {board}')
    recipe = firmwares.recipe_for(board, hardware)
    outputs = firmwares.ensure(board, Clock(), Progress(), hardware=hardware)
    cache = Path(cache) if cache is not None else build.cache_root()
    target = cache / 'private/ram' / f'{board}-{receipt["files"]["linux.bin"][:16]}'
    if target.is_dir():
        try:
            verify_ram(target)
            return target
        except (OSError, ValueError):
            shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix='firmware-', dir=target.parent) as temporary:
        work = Path(temporary)
        root = work / 'root'
        for item in recipe['outputs']:
            destination = root / PurePosixPath(item['destination'])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(outputs / item['sha256'], destination)
        initramfs.write(initramfs.entries([root]), work / 'firmware.cpio.gz')
        combined = work / 'initramfs.cpio.gz'
        with combined.open('wb') as stream:
            for part in (output / 'initramfs.cpio.gz', work / 'firmware.cpio.gz'):
                stream.write(part.read_bytes())
        assembly = receipt['assembly']
        profile = work / 'boot.json'
        profile.write_text(json.dumps({'bootargs': 'chosen.bootargs=' + assembly['bootargs'] + '\n'}))
        image = work / 'image'
        image.mkdir()
        assemble(profile, Path(assembly['loader']), [Path(path) for path in assembly['dtbs']],
                 Path(assembly['kernel']), combined, image / 'linux.bin')
        shutil.copyfile(output / 'pongo.bin', image / 'pongo.bin')
        layered = {**receipt, 'boards': [board], 'firmware': True,
                   'files': {name: digest(image / name) for name in ('linux.bin', 'pongo.bin')}}
        layered.pop('overlay', None)
        layered['sources'] = {}
        (image / 'build.json').write_text(json.dumps(layered, indent=2) + '\n')
        image.rename(target)
    return target


def verify_ram(output):
    output = Path(output).resolve()
    receipt = json.loads((output / 'build.json').read_text())
    if 'device' in receipt and 'boards' not in receipt:  # built before payloads were per platform
        receipt['boards'], receipt['platform'] = [receipt['device']], None
    if (receipt.get('kind') not in (None, 'orchard.ram-build') or not receipt.get('boards') or
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



"""Launch an installed system from its boot entry on the device.

The device carries the boot contract (hardware docs/boot-contract.md): an
XBOOTLDR FAT32 partition with Boot Loader Specification entries. The host
boots a storage-capable m1n1, reads the entries through m1n1's generic
main_storage calls, lets the user choose, has m1n1 copy the chosen kernel,
initrd and DTB from storage into memory, and boots it. Nothing is written.

The host brings only Pongo and that m1n1, which ship with Orchard
(orchard.hardware.loader) or are built from the hardware source, so any
host with Orchard can launch any installed system, from any distro.
"""
import struct

from .bootdisk import BootDiskError, Disk, read_boot
from .devices import choose
from .progress import Clock, Progress
from .service import ask, save_report
from .state import exclusive_operation, private_directory
from .state import DeviceState
from .transports.m1n1 import BLOCK, M1n1Error, M1n1Proxy

KERNEL_ALIGN = 2 << 20
KERNEL_SPACE = 512 << 20


def describe(number, entry, entries):
    # The title alone, unless another entry has the same one.
    same = [e for e in entries if e.title == entry.title]
    line = f'  {number}. {entry.title}' + (f' ({entry.get("version")})' if len(same) > 1 else '')
    if entry.problems:
        line += ' - not bootable: ' + '; '.join(entry.problems)
    return line


def select(entries, wanted, clock, input_fn):
    if wanted is not None:
        matches = [e for e in entries if e.id == wanted]
        if not matches:
            raise ValueError(f'No boot entry {wanted!r} on this device')
        return matches[0]
    bootable = [e for e in entries if not e.problems]
    if not bootable:
        raise ValueError('No bootable entry on this device; nothing was started')
    if len(entries) == 1:
        return entries[0]
    for number, entry in enumerate(entries, 1):
        print(describe(number, entry, entries))
    default = entries.index(bootable[0]) + 1
    answer = ask(clock, f'Boot which system? [{default}] ', input_fn) or str(default)
    if not answer.isdecimal() or not 1 <= int(answer) <= len(entries):
        raise ValueError('No boot entry selected')
    return entries[int(answer) - 1]


def load(proxy, fat, path, align=BLOCK):
    runs, size = fat.file(path)
    addr = proxy.memalign(align, max(size, 1))
    proxy.load_runs(runs, addr)
    return addr, size


SPLASH_MAGIC = b'OSPL'


def paint_splash(proxy, fat, entry, progress):
    """Paint the boot entry's splash, centred on black, before its kernel loads.

    `splash` next to the entry's kernel: b'OSPL', u32 width, u32 height, then
    width x height pixels as bytes r, g, b, 0. The quiet m1n1 shows black
    until then; the kernel keeps this frame until the system clears it.
    Anything wrong here only costs the splash, never the boot.
    """
    path = entry.get('linux').rsplit('/', 1)[0] + '/splash'
    try:
        addr, size = load(proxy, fat, path)
        magic, width, height = struct.unpack('<4sII', proxy.read(addr, 12))
        screen_width, screen_height = proxy.screen_size()
        if magic != SPLASH_MAGIC or size != 12 + width * height * 4 or width > screen_width or height > screen_height:
            raise ValueError(f'{path}: not a splash for this screen')
        proxy.fill(0, 0, screen_width, screen_height, 0)
        proxy.blit((screen_width - width) // 2, (screen_height - height) // 2, width, height, addr + 12)
    except (BootDiskError, M1n1Error, ValueError, struct.error) as error:
        progress.diagnostic(f'splash: {error}')


def start(proxy, fat, entry, progress):
    progress.phase('Starting ' + entry.title)
    paint_splash(proxy, fat, entry, progress)
    runs, _ = fat.file(entry.get('linux'))
    kernel = proxy.memalign(KERNEL_ALIGN, KERNEL_SPACE)
    if fat.disk.read(runs[0][0], 2) == b'\x1f\x8b':
        image, image_size = load(proxy, fat, entry.get('linux'))
        proxy.gzdec(image, image_size, kernel, KERNEL_SPACE)
    else:
        proxy.load_runs(runs, kernel)
    dtb, _ = load(proxy, fat, entry.get('devicetree'))
    initrds = entry.keys.get('initrd', [])
    initrd_size = sum(fat.file(path)[1] for path in initrds)
    initrd = proxy.memalign(1 << 16, max(initrd_size, 1))
    offset = 0
    for path in initrds:
        # Concatenated initrds are one cpio stream to Linux, as BLS specifies.
        offset += proxy.load_runs(fat.file(path)[0], initrd + offset)
    proxy.storage_shutdown()
    proxy.boot_linux(kernel, dtb, initrd, initrd_size, entry.get('options'))


def loader_files(profile, progress):
    """Pongo and the storage-capable m1n1: shipped with Orchard, else built from the hardware source."""
    from . import hardware
    files = hardware.loader(profile)
    if files is None:
        from .build import prepare_loader
        progress.phase('Preparing the bootloader from the hardware source')
        files = prepare_loader(device=profile)
    return files


@exclusive_operation
def launch(*, entry=None, input_fn=input, progress=None):
    clock, progress = Clock(), progress or Progress()
    adapter, device = choose()
    if device['mode'] not in ('dfu', 'pongo'):
        raise ValueError(f'Put the {adapter.NAME} in DFU mode to launch an installed system')
    # One step for the user: no transfer progress, only what to do if the device stalls.
    port = adapter.start_loader(device, loader_files(adapter.PROFILE, progress), progress, quiet=True)
    report = {'profile': adapter.PROFILE, 'ecid': device['ecid'], 'operation': 'launch',
              'entry': None, 'started': False}
    path = private_directory(DeviceState(adapter.PROFILE, device['ecid']).root) / 'last-launch.json'
    with M1n1Proxy(port) as proxy:
        proxy.storage_init()
        try:
            _, fat, entries = read_boot(Disk(proxy.read_block, BLOCK), adapter.DEVICETREE)
            if not entries:
                raise ValueError('No boot entries on this device; install a system first')
            chosen = select(entries, entry, clock, input_fn)
            if chosen.problems:
                raise ValueError(f'{chosen.title} is not bootable: ' + '; '.join(chosen.problems))
        except (BootDiskError, ValueError, M1n1Error):
            proxy.storage_shutdown()
            raise
        report['entry'] = {'id': chosen.id, 'title': chosen.title, 'version': chosen.get('version'),
                           'root': chosen.root}
        start(proxy, fat, chosen, progress)
    report.update(started=True, timing=clock.result())
    save_report(path, report)
    progress.phase(f'{chosen.title} is starting.')
    return report

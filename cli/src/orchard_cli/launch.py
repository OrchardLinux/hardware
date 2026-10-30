"""Launch an installed system from its boot entry on the device.

The device carries the boot contract (hardware docs/boot-contract.md): an
XBOOTLDR FAT32 partition with Boot Loader Specification entries. The host
boots a storage-capable m1n1, reads the entries through m1n1's generic
main_storage calls, lets the user choose, has m1n1 copy the chosen kernel,
initrd and DTB from storage into memory, and boots it. Nothing is written.

The host brings only Pongo and that m1n1, which ship with Orchard
(orchard_hardware.loader) or are built from the hardware source, so any
host with Orchard can launch any installed system, from any distro.
"""
from .bootdisk import BootDiskError, Disk, read_boot
from .devices import choose
from .progress import Clock, Progress
from .service import ask, save_report
from .state import exclusive_operation, private_directory
from .state import DeviceState
from .transports.m1n1 import BLOCK, M1n1Error, M1n1Proxy

KERNEL_ALIGN = 2 << 20
KERNEL_SPACE = 512 << 20


def describe(number, entry):
    detail = ' · '.join(x for x in (entry.get('version'), entry.root and 'root ' + entry.root[:8]) if x)
    line = f'  {number}. {entry.title}' + (f' · {detail}' if detail else '') + f'  [{entry.id}]'
    if entry.problems:
        line += '\n       not bootable: ' + '; '.join(entry.problems)
    return line


def select(entries, wanted, clock, input_fn):
    if wanted is not None:
        matches = [e for e in entries if e.id == wanted]
        if not matches:
            raise ValueError(f'No boot entry {wanted!r} on this device')
        return matches[0]
    for number, entry in enumerate(entries, 1):
        print(describe(number, entry))
    bootable = [e for e in entries if not e.problems]
    if not bootable:
        raise ValueError('No bootable entry on this device; nothing was started')
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


def start(proxy, fat, entry, progress):
    progress.phase('Loading ' + entry.title + ' from the device')
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
    progress.phase('Starting ' + entry.title)
    proxy.boot_linux(kernel, dtb, initrd, initrd_size, entry.get('options'))


def loader_files(profile, progress):
    """Pongo and the storage-capable m1n1: shipped with Orchard, else built from the hardware source."""
    import orchard_hardware
    files = orchard_hardware.loader(profile)
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
    port = adapter.start_loader(device, loader_files(adapter.PROFILE, progress), progress)
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
    print(f'{chosen.title} is starting; the bootloader has handed over to its kernel.')
    return report

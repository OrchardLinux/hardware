"""Launch reads boot entries from the device through m1n1 and boots one; it never writes."""
import gzip
import io
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import ANY, Mock, patch

from boot_image import ROOT_ARM64, XBOOTLDR, fat32, gpt_disk
from orchard_cli import launch
from orchard_cli.progress import Progress

ROOT = uuid.UUID('4d6c1a2e-0000-4000-8000-000000000001')
KERNEL = b'ARM64 Image ' * 4000


def entry(title, version, root=ROOT, devicetree='t8010-j71b.dtb'):
    return (f'title {title}\nversion {version}\nsort-key {title.lower()}\n'
            f'linux /t/{version}/linux\ninitrd /t/{version}/initrd\ninitrd /t/{version}/extra\n'
            f'devicetree /t/{version}/{devicetree}\noptions root=PARTUUID={root} rw\n')


def image(entries, kernel=KERNEL):
    files = {}
    for name, text in entries.items():
        files['/loader/entries/' + name] = text.encode()
        version = text.split('version ')[1].split('\n')[0]
        files[f'/t/{version}/linux'] = kernel
        files[f'/t/{version}/initrd'] = b'initrd-one' * 900
        files[f'/t/{version}/extra'] = b'initrd-two' * 7
        files[f'/t/{version}/t8010-j71b.dtb'] = b'\xd0\x0d\xfe\xed' + b'D' * 500
    return gpt_disk([(XBOOTLDR, uuid.uuid4(), 'boot', fat32(files)),
                     (ROOT_ARM64, ROOT, 'root', b'\0' * 4096)])


class FakeM1n1:
    """m1n1 proxy over a disk image: storage reads only, device memory as a dict."""

    def __init__(self, disk):
        self.disk, self.memory, self.heap = disk, {}, 0x900000000
        self.calls, self.booted = [], None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def storage_init(self):
        self.calls.append('init')

    def storage_shutdown(self):
        self.calls.append('shutdown')

    def read_block(self, lba):
        return self.disk[lba * 4096:(lba + 1) * 4096]

    def memalign(self, align, size):
        addr = (self.heap + align - 1) & ~(align - 1)
        self.heap = addr + size
        return addr

    def load_runs(self, runs, addr):
        data = b''.join(self.disk[start:start + length] for start, length in runs)
        self.memory[addr] = data
        return len(data)

    def read(self, addr, size):
        return self.memory[addr][:size]

    def gzdec(self, src, size, dst, dst_size):
        self.memory[dst] = gzip.decompress(self.memory[src][:size])
        return len(self.memory[dst])

    def boot_linux(self, kernel, dtb, initrd, initrd_size, bootargs):
        self.calls.append('boot')
        initrd_data = b''.join(data for addr, data in sorted(self.memory.items())
                               if initrd <= addr < initrd + initrd_size)
        self.booted = dict(kernel=self.memory[kernel], dtb=self.memory[dtb], initrd=initrd_data,
                           bootargs=bootargs)


LOADER = {'pongo': Path('/loaders/pongo.bin'), 'm1n1': Path('/loaders/j71b/m1n1.bin')}


class LaunchFlow(unittest.TestCase):
    def run_launch(self, disk, answers=('',), entry_id=None, mode='dfu', proxy=None):
        adapter = Mock(PROFILE='j71b', NAME='iPad', DEVICETREE='t8010-j71b.dtb')
        adapter.start_loader.return_value = '/dev/cu.usbmodemTEST1'
        proxy = proxy or FakeM1n1(disk)
        replies = iter(answers)
        with tempfile.TemporaryDirectory() as home, patch.dict(os.environ, ORCHARD_HOME=home), \
                patch.object(launch, 'choose', return_value=(adapter, {'mode': mode, 'ecid': 1})), \
                patch.object(launch, 'loader_files', return_value=LOADER) as loader, \
                patch.object(launch, 'M1n1Proxy', return_value=proxy):
            report = launch.launch(entry=entry_id, input_fn=lambda _: next(replies),
                                   progress=Progress(io.StringIO()))
        loader.assert_called_once_with('j71b', ANY)
        adapter.start_loader.assert_called_once_with({'mode': mode, 'ecid': 1}, LOADER, ANY)
        return report, proxy

    def test_boots_the_chosen_entry_from_device_storage(self):
        disk = image({'omarchy-2.conf': entry('Omarchy', '6.18.2'), 'omarchy-1.conf': entry('Omarchy', '6.17.9')})
        report, proxy = self.run_launch(disk, answers=('2',))
        self.assertTrue(report['started'])
        self.assertEqual(report['entry']['version'], '6.17.9')
        self.assertEqual(proxy.booted['kernel'], KERNEL)
        self.assertEqual(proxy.booted['initrd'], b'initrd-one' * 900 + b'initrd-two' * 7)
        self.assertTrue(proxy.booted['dtb'].startswith(b'\xd0\x0d\xfe\xed'))
        self.assertEqual(proxy.booted['bootargs'], f'root=PARTUUID={ROOT} rw')
        self.assertEqual(proxy.calls, ['init', 'shutdown', 'boot'])

    def test_default_is_the_first_bootable_entry_in_bls_order(self):
        disk = image({'a.conf': entry('Omarchy', '6.17.9'), 'b.conf': entry('Omarchy', '6.18.2')})
        report, _ = self.run_launch(disk)
        self.assertEqual(report['entry']['version'], '6.18.2')

    def test_gzip_kernels_are_decompressed_on_the_device(self):
        disk = image({'a.conf': entry('Omarchy', '6.18.2')}, kernel=gzip.compress(KERNEL))
        _, proxy = self.run_launch(disk)
        self.assertEqual(proxy.booted['kernel'], KERNEL)

    def test_entry_option_skips_the_question(self):
        disk = image({'omarchy.conf': entry('Omarchy', '6.18.2'), 'minimal.conf': entry('Minimal', '6.18.2')})
        report, _ = self.run_launch(disk, answers=(), entry_id='minimal')
        self.assertEqual(report['entry']['title'], 'Minimal')

    def test_unbootable_entries_are_listed_and_refused(self):
        disk = image({'other.conf': entry('Other', '6.18.2', devicetree='t8010-j171.dtb')})
        with self.assertRaisesRegex(ValueError, 'No bootable entry'):
            self.run_launch(disk)

    def test_storage_is_released_when_nothing_boots(self):
        disk = image({})
        proxy = FakeM1n1(disk)
        with self.assertRaisesRegex(ValueError, 'No boot entries'):
            self.run_launch(disk, proxy=proxy)
        self.assertEqual(proxy.calls, ['init', 'shutdown'])

    def test_running_systems_are_not_rebooted(self):
        with self.assertRaisesRegex(ValueError, 'DFU mode'):
            self.run_launch(image({}), mode='linux')

    def test_command_accepts_launch_without_arguments(self):
        from orchard_cli import command
        with patch('sys.argv', ['orchard', 'launch']), patch.object(launch, 'launch', return_value={}) as run:
            command.main()
        run.assert_called_once_with(entry=None, progress=ANY)

    def test_loader_ships_with_orchard_or_is_built(self):
        # Launching needs nothing from a distro or an install: only Pongo and m1n1.
        import orchard_hardware
        from orchard_cli import build
        with patch.object(orchard_hardware, 'loader', return_value=LOADER), \
                patch.object(build, 'prepare_loader') as built:
            self.assertEqual(launch.loader_files('j71b', Progress(io.StringIO())), LOADER)
        built.assert_not_called()
        with patch.object(orchard_hardware, 'loader', return_value=None), \
                patch.object(build, 'prepare_loader', return_value=LOADER) as built:
            self.assertEqual(launch.loader_files('j71b', Progress(io.StringIO())), LOADER)
        built.assert_called_once_with(device='j71b')


if __name__ == '__main__':
    unittest.main()

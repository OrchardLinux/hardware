"""Starting an installed root in the recovery's boot: which partition, which entry."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orchard_start_test', ROOT / 'runtime/recovery/start.py')
start = importlib.util.module_from_spec(spec)
spec.loader.exec_module(start)

ROOTFS = '6f3c2a10-1111-4222-8333-444455556666'
OTHER = '7a7a7a7a-1111-4222-8333-444455556666'
RELEASE = '7.3.0-rc1-orchard-apple-16k'


def entry(version, root):
    return (f'title Omarchy\nversion {version}\nlinux /omarchy/{version}/linux\n'
            f'options root=PARTUUID={root} rw rootwait\n')


class Start(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        (self.root / 'loader/entries').mkdir(parents=True)

    def test_entry_must_boot_the_running_kernel(self):
        (self.root / 'loader/entries/a.conf').write_text(entry('6.0-old', OTHER))
        (self.root / 'loader/entries/b.conf').write_text(entry(RELEASE, ROOTFS))
        self.assertEqual(start.entry_for(self.root, ROOTFS, RELEASE), 'b.conf')
        with self.assertRaisesRegex(ValueError, 'orchard launch'):
            start.entry_for(self.root, OTHER, RELEASE)
        with self.assertRaisesRegex(ValueError, 'no entry'):
            start.entry_for(self.root, '00000000-0000-4000-8000-000000000000', RELEASE)

    def test_partition_is_found_by_partuuid(self):
        for name, partuuid in (('nvme0n1p1', OTHER), ('nvme0n1p3', ROOTFS.upper())):
            (self.root / name).mkdir()
            (self.root / name / 'uevent').write_text(f'MAJOR=259\nDEVNAME={name}\nPARTUUID={partuuid}\n')
        with patch.object(start, 'SYSFS', self.root):
            self.assertEqual(start.partition(ROOTFS), '/dev/nvme0n1p3')
            with self.assertRaisesRegex(ValueError, 'No partition'):
                start.partition('00000000-0000-4000-8000-000000000000')

    def test_init_switches_only_after_a_successful_check(self):
        init = (ROOT / 'boot/initramfs/recovery/init').read_text()
        self.assertLess(init.index('start.py'), init.index('kill -TERM'))
        self.assertLess(init.index('kill -KILL'), init.index('exec switch_root /newroot /sbin/init'))
        self.assertIn('mv /run/orchard/start.json /run/orchard/start.failed', init)


if __name__ == '__main__':
    unittest.main()


class Partition(unittest.TestCase):
    """The root to start is found through its disk's GPT, as orchard's initrd does."""
    def test_a_partuuid_names_the_partition_of_its_gpt_entry(self):
        import sys as system
        system.path.insert(0, str(ROOT / 'tests'))
        from test_root_initrd import gpt
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            dev, sysfs = work / 'dev', work / 'sys/class/block'
            dev.mkdir()
            gpt(dev / 'nvme0n1', 4096, [OTHER, ROOTFS])
            disk = work / 'sys/devices/nvme0n1'
            (disk / 'queue').mkdir(parents=True)
            (disk / 'queue/logical_block_size').write_text('4096\n')
            sysfs.mkdir(parents=True)
            (sysfs / 'nvme0n1').symlink_to(disk)
            for number in (1, 2):
                (disk / f'nvme0n1p{number}').mkdir()
                (disk / f'nvme0n1p{number}/partition').write_text(f'{number}\n')
                (sysfs / f'nvme0n1p{number}').symlink_to(disk / f'nvme0n1p{number}')
            self.assertEqual(start.partition(ROOTFS.upper(), sysfs, dev), str(dev / 'nvme0n1p2'))
            with self.assertRaisesRegex(ValueError, 'No partition'):
                start.partition('00000000-1111-4222-8333-444455556666', sysfs, dev)

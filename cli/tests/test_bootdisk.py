import unittest
import uuid

from boot_image import ROOT_ARM64, XBOOTLDR, fat32, gpt_disk
from orchard_cli.bootdisk import BootDiskError, Disk, read_boot

ROOT = uuid.UUID('4d6c1a2e-0000-4000-8000-000000000001')
ENTRY = f"""# Omarchy
title      Omarchy
version    6.18.2-orchard
sort-key   omarchy
linux      /6a1f/6.18.2-orchard/linux
initrd     /6a1f/6.18.2-orchard/initrd
devicetree /6a1f/6.18.2-orchard/t8010-j71b.dtb
options    root=PARTUUID={ROOT} rw quiet
"""


def disk_for(files, *, roots=(ROOT,), boots=1):
    parts = [(XBOOTLDR, uuid.uuid4(), 'boot', fat32(files)) for _ in range(boots)]
    parts += [(ROOT_ARM64, root, 'root', b'\0' * 4096) for root in roots]
    image = gpt_disk(parts)
    return Disk(lambda lba: image[lba * 4096:(lba + 1) * 4096]), image


def files(entry=ENTRY, name='omarchy-6.18.2.conf'):
    return {
        '/loader/entries/' + name: entry.encode(),
        '/6a1f/6.18.2-orchard/linux': b'K' * 10000,
        '/6a1f/6.18.2-orchard/initrd': b'I' * 5000,
        '/6a1f/6.18.2-orchard/t8010-j71b.dtb': b'D' * 300,
    }


class BootDisk(unittest.TestCase):
    def test_valid_entry_and_file_extents(self):
        disk, image = disk_for(files())
        _, fat, entries = read_boot(disk, devicetree='t8010-j71b.dtb')
        self.assertEqual([e.id for e in entries], ['omarchy-6.18.2'])
        entry = entries[0]
        self.assertEqual(entry.problems, [])
        self.assertEqual(entry.title, 'Omarchy')
        self.assertEqual(entry.root, str(ROOT))
        runs, size = fat.file(entry.get('linux'))
        self.assertEqual(size, 10000)
        self.assertEqual(b''.join(image[start:start + length] for start, length in runs), b'K' * 10000)

    def test_contract_violations_are_reported_not_hidden(self):
        bad = ENTRY.replace(f'root=PARTUUID={ROOT}', 'root=/dev/nvme0n1p3') + 'devicetree-overlay /x.dtbo\n'
        disk, _ = disk_for(files(bad))
        _, _, entries = read_boot(disk, devicetree='t8010-j72b.dtb')
        problems = entries[0].problems
        self.assertIn('options lack root=PARTUUID=', problems)
        self.assertIn('devicetree-overlay is not supported', problems)
        self.assertIn('devicetree is not t8010-j72b.dtb', problems)

    def test_root_must_be_an_arm64_root_partition(self):
        disk, _ = disk_for(files(), roots=())
        _, _, entries = read_boot(disk)
        self.assertIn('root=PARTUUID is not an arm64 root partition on this disk', entries[0].problems)

    def test_missing_files_are_reported(self):
        content = files()
        del content['/6a1f/6.18.2-orchard/initrd']
        disk, _ = disk_for(content)
        _, _, entries = read_boot(disk)
        self.assertTrue(any('initrd: missing' in p for p in entries[0].problems))

    def test_exactly_one_boot_partition(self):
        disk, _ = disk_for(files(), boots=2)
        with self.assertRaisesRegex(BootDiskError, 'exactly one XBOOTLDR'):
            read_boot(disk)
        disk, _ = disk_for(files(), boots=0)
        with self.assertRaisesRegex(BootDiskError, 'exactly one XBOOTLDR'):
            read_boot(disk)

    def test_bls_order(self):
        older = ENTRY.replace('6.18.2-orchard', '6.17.9-orchard')
        content = {**files(), **files(older, 'omarchy-6.17.9.conf')}
        disk, _ = disk_for(content)
        _, _, entries = read_boot(disk)
        self.assertEqual([e.get('version') for e in entries], ['6.18.2-orchard', '6.17.9-orchard'])


if __name__ == '__main__':
    unittest.main()

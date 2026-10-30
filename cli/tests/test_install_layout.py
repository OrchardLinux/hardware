"""Install targets per disk layout (hardware docs/boot-contract.md)."""
import unittest

from orchard_cli import install_layout as L

APFS = '7c3457ef-0000-11aa-aa11-00306543ecac'
IMAGE = {'bytes': 1_079_804_265, 'sha256': 'a' * 64, 'expanded_bytes': 2_745_833_606, 'compression': 'zstd'}
ENTRY = {'id': 'omarchy', 'title': 'Omarchy', 'version': '7.3.0-rc1-orchard-apple-16k'}


def part(number, start, end, type_, **extra):
    return {'number': number, 'start': start, 'blocks': end - start + 1, 'type': type_,
            'partuuid': f'00000000-0000-4000-8000-{number:012d}', **extra}


def layout(partitions, free):
    return {'disk': {'path': '/dev/nvme0n1', 'sector': 4096, 'table_sha256': 'f' * 64},
            'partitions': partitions, 'free': [{'start': s, 'blocks': b} for s, b in free]}


APFS_PART = part(1, 6, 14648442, APFS)
END = 31249994


class InstallLayout(unittest.TestCase):
    def test_a_free_space_creates_boot_and_root(self):
        disk = layout([APFS_PART], [(14648443, END - 14648443 + 1)])
        (option,) = L.options(disk)
        self.assertEqual(option.kind, 'free')
        self.assertTrue(option.creates_boot)
        plan = L.plan(disk, option, IMAGE, ENTRY)
        self.assertIsNone(plan['remove'])
        self.assertTrue(plan['boot']['create'])
        self.assertEqual(plan['boot']['start'] % 256, 0)          # 1 MiB aligned
        self.assertEqual(plan['boot']['blocks'] * 4096, L.BOOT_BYTES)
        self.assertEqual(plan['root']['start'], plan['boot']['start'] + plan['boot']['blocks'])
        self.assertLessEqual(plan['root']['start'] + plan['root']['blocks'], END + 1)

    def test_b_old_linux_partition_can_be_replaced_never_apfs(self):
        old = part(3, 15690544, END, L.LINUX_DATA, fs='ext4', label='OrchardLinux')
        disk = layout([APFS_PART, old], [])
        options = L.options(disk)
        self.assertEqual([o.kind for o in options], ['replace'])
        self.assertIn('erases it', options[0].description)
        plan = L.plan(disk, options[0], IMAGE, ENTRY)
        self.assertEqual(plan['remove']['number'], 3)
        self.assertTrue(plan['boot']['create'])
        self.assertGreaterEqual(plan['boot']['start'], old['start'])

    def test_c_existing_boot_is_reused_and_root_replaced(self):
        boot = part(2, 14648576, 14910719, L.XBOOTLDR, fs='vfat')
        root = part(4, 14910720, END, L.ROOT_ARM64, fs='ext4')
        disk = layout([APFS_PART, boot, root], [])
        (option,) = L.options(disk)
        self.assertEqual(option.partition['number'], 4)
        self.assertFalse(option.creates_boot)
        plan = L.plan(disk, option, IMAGE, ENTRY)
        self.assertEqual(plan['boot'], {'create': False, 'partuuid': boot['partuuid'], 'number': 2})
        self.assertEqual(plan['remove']['partuuid'], root['partuuid'])

    def test_d_small_or_foreign_partitions_are_never_targets(self):
        tiny = part(2, 14648443, 14700000, L.LINUX_DATA)                      # far below 9 GiB
        other = part(3, 15690544, END, 'c12a7328-f81f-11d2-ba4b-00a0c93ec93b')  # ESP, not ours
        disk = layout([APFS_PART, tiny, other], [(14700001, 1000)])
        self.assertEqual(L.options(disk), [])

    def test_two_boot_partitions_are_refused(self):
        disk = layout([APFS_PART, part(2, 14648576, 14910719, L.XBOOTLDR),
                       part(3, 14910720, 15172863, L.XBOOTLDR)], [])
        with self.assertRaisesRegex(L.LayoutError, 'more than one XBOOTLDR'):
            L.options(disk)

    def test_image_must_fit_with_headroom(self):
        disk = layout([APFS_PART], [(14648443, (L.BOOT_BYTES + L.MIN_ROOT_BYTES) // 4096 + 512)])
        (option,) = L.options(disk)
        with self.assertRaisesRegex(L.LayoutError, 'too small for this system image'):
            L.plan(disk, option, {**IMAGE, 'expanded_bytes': 7 * L.GIB}, ENTRY)


if __name__ == '__main__':
    unittest.main()

"""The CLI and hardware's kernel/build agree on one incremental kernel tree."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchard import kernel


class KernelTree(unittest.TestCase):
    def directory(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        return Path(temporary.name)

    def hardware(self, builder):
        root = self.directory()
        (root / 'kernel').mkdir()
        (root / 'kernel/upstream.lock').write_text('url=https://github.com/torvalds/linux.git\n'
                                                   'commit=' + 'c' * 40 + '\n')
        (root / 'kernel/build').write_text(builder)
        return root

    def setUp(self):
        self.cache = self.directory()
        for patcher in (patch.dict(os.environ, ORCHARD_CACHE_DIR=str(self.cache)),
                        patch.object(kernel, 'layout', return_value=('apple-16k', 't8010-j71b'))):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_current_hardware_shares_one_tree_per_platform_and_pin(self):
        one, two = self.hardware('... --work-dir ...'), self.hardware('... --work-dir ...')
        expected = self.cache.resolve() / 'kernel-work' / ('apple-16k-' + 'c' * 12)
        self.assertEqual(kernel.work_directory(one, 'j71b'), expected)
        self.assertEqual(kernel.work_directory(two, 'j71b'), expected)

    def test_older_hardware_keeps_its_tree_in_the_checkout(self):
        hardware = self.hardware('... --resume ...')
        self.assertEqual(kernel.work_directory(hardware, 'j71b'), hardware / 'kernel/.work/apple-16k')

    def test_lock_names_its_tree_for_kernel_build_and_restores_the_environment(self):
        hardware = self.hardware('... --work-dir ...')
        self.assertNotIn('ORCHARD_KERNEL_WORK_LOCKED', os.environ)
        with kernel.tree_lock(hardware, 'j71b') as work:
            self.assertEqual(os.environ['ORCHARD_KERNEL_WORK_LOCKED'], str(work))
            self.assertTrue((work / '.lock').exists())
        self.assertNotIn('ORCHARD_KERNEL_WORK_LOCKED', os.environ)


if __name__ == '__main__':
    unittest.main()

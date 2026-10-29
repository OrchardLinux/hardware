"""Installed hardware retains the recipes used by the shared kernel producer."""
from pathlib import Path
import subprocess
import sys
import unittest

import orchard_hardware

SOURCE = Path(__file__).resolve().parents[1]
PACKAGE = Path(orchard_hardware.__file__).resolve().parent


class PackageResources(unittest.TestCase):
    def test_nfs_runtime_is_available_from_the_installed_package(self):
        for source in (SOURCE / 'boot/initramfs/nfs').iterdir():
            if source.is_file():
                relative = source.relative_to(SOURCE)
                with self.subTest(resource=str(relative)):
                    packaged = PACKAGE / relative
                    self.assertTrue(packaged.is_file(), str(packaged))
                    self.assertEqual(packaged.read_bytes(), source.read_bytes())

    def test_platform_recipes_and_patch_layers_match_the_source(self):
        files = [SOURCE / name for name in ('kernel/series.py', 'kernel/sources.toml',
                                            'kernel/upstream.lock')]
        for relative, pattern in (('kernel/series', '*'), ('kernel/configs', '*/config'),
                                  ('kernel/patches', '**/*.patch')):
            files.extend(path for path in (SOURCE / relative).glob(pattern) if path.is_file())
        for source in files:
            relative = source.relative_to(SOURCE)
            with self.subTest(resource=str(relative)):
                packaged = PACKAGE / relative
                self.assertTrue(packaged.is_file(), str(packaged))
                self.assertEqual(packaged.read_bytes(), source.read_bytes())

    def test_n61_recipe_resolves_inside_the_installed_package(self):
        self.assertIn('n61', orchard_hardware.profiles())
        resolver = PACKAGE / 'kernel/series.py'
        resolved = subprocess.check_output([sys.executable, str(resolver), '--resolve', 'n61'],
                                           text=True).strip()
        self.assertEqual(resolved, 'apple-4k t7000-n61')
        patches = subprocess.check_output([sys.executable, str(resolver), 'n61'], text=True).splitlines()
        self.assertTrue(patches)
        for name in patches:
            path = Path(name).resolve()
            with self.subTest(patch=name):
                self.assertIn(PACKAGE, path.parents)
                self.assertTrue(path.is_file())


if __name__ == '__main__':
    unittest.main()

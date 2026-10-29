"""External modules use the SDK for the selected kernel platform."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ModuleSDKPlatform(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.source = root / 'source'
        self.source.mkdir()
        self.build = root / 'build'
        (self.build / 'include/config').mkdir(parents=True)
        (self.build / 'Module.symvers').write_text('')
        self.release = self.build / 'include/config/kernel.release'
        self.config = self.build / '.config'
        self.modules = root / 'modules'
        (self.modules / 'example').mkdir(parents=True)
        (self.modules / 'example/Makefile').write_text('obj-m += example.o\n')
        self.output = root / 'external'
        mock = root / 'mock/orchard_hardware'
        mock.mkdir(parents=True)
        (mock / '__init__.py').write_text('')
        (mock / 'buildenv.py').write_text('print("fake compiler invoked")\n')
        self.env = dict(os.environ, PYTHONPATH=str(root / 'mock'))

    def build_modules(self, suffix):
        return subprocess.run(['bash', str(ROOT / 'kernel/build-modules'),
                               '--source', str(self.source), '--build', str(self.build),
                               '--modules', str(self.modules), '--localversion', suffix,
                               '--output', str(self.output)], env=self.env,
                              capture_output=True, text=True)

    def test_4k_sdk_is_accepted_for_4k_modules(self):
        self.release.write_text('7.3.0-rc1-orchard-apple-4k\n')
        self.config.write_text('CONFIG_ARM64_4K_PAGES=y\n')
        result = self.build_modules('-orchard-apple-4k')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.output / 'example/Makefile').read_text(), 'obj-m += example.o\n')

    def test_16k_sdk_is_accepted_for_existing_platform_modules(self):
        self.release.write_text('7.3.0-rc1-orchard-apple-16k\n')
        self.config.write_text('CONFIG_ARM64_16K_PAGES=y\n')
        result = self.build_modules('-orchard-apple-16k')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_or_page_size_mismatch_stops_before_compilation(self):
        for release, config in (('7.3.0-rc1-orchard-apple-16k', 'CONFIG_ARM64_4K_PAGES=y'),
                                ('7.3.0-rc1-orchard-apple-4k', 'CONFIG_ARM64_16K_PAGES=y')):
            with self.subTest(release=release, config=config):
                self.release.write_text(release + '\n')
                self.config.write_text(config + '\n')
                result = self.build_modules('-orchard-apple-4k')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('fake compiler invoked', result.stdout)
                self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()

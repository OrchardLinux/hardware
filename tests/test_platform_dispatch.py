"""Exercise shared kernel dispatch without compiling a kernel."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]


class PlatformDispatch(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        kernel = self.root / 'kernel'
        (kernel / 'series').mkdir(parents=True)
        (kernel / 'platforms').mkdir()
        (kernel / 'p105').mkdir()
        for name in ('build', 'series.py'):
            shutil.copy2(SOURCE / 'kernel' / name, kernel / name)
        (kernel / 'series/apple-a5').write_text('# legacy recipe\n')
        (kernel / 'platforms/apple-a5.toml').write_text('schema = 1\nproducer = "p105/build"\ndtb = "p105ap.dtb"\n')
        descriptor = self.root / 'devices/test-device/device.toml'
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text('[kernel]\nplatform = "apple-a5"\ndtb = "p105ap"\n')
        producer = kernel / 'p105/build'
        producer.write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n')

    def test_device_dispatch_forwards_legacy_arguments(self):
        result = subprocess.run([str(self.root / 'kernel/build'), 'test-device',
                                 '--work', '/tmp/example', '--output', '/tmp/result'],
                                capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout),
                         ['--work', '/tmp/example', '--output', '/tmp/result'])

    def test_unknown_producer_stops_before_running_it(self):
        (self.root / 'kernel/platforms/apple-a5.toml').write_text('schema = 1\nproducer = "unknown"\n')
        result = subprocess.run([str(self.root / 'kernel/build'), 'test-device', '--help'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unsupported platform producer', result.stderr)

    def test_standard_arm64_resolution_needs_no_optional_toml_library(self):
        (self.root / 'kernel/series/apple-4k').write_text('# upstream arm64\n')
        result = subprocess.run([sys.executable, '-S', str(self.root / 'kernel/series.py'),
                                 '--producer', 'apple-4k'], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), 'linux-arm64')

    def test_broken_platform_metadata_link_does_not_select_arm64(self):
        metadata = self.root / 'kernel/platforms/apple-a5.toml'
        metadata.unlink()
        metadata.symlink_to(self.root / 'missing.toml')
        result = subprocess.run([str(self.root / 'kernel/build'), 'test-device', '--help'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid platform metadata', result.stderr)

    def test_device_tree_mismatch_and_linked_producer_are_rejected(self):
        metadata = self.root / 'kernel/platforms/apple-a5.toml'
        metadata.write_text('schema = 1\nproducer = "p105/build"\ndtb = "other.dtb"\n')
        result = subprocess.run([str(self.root / 'kernel/build'), 'test-device', '--help'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('device tree differs', result.stderr)
        metadata.write_text('schema = 1\nproducer = "p105/build"\ndtb = "p105ap.dtb"\n')
        producer = self.root / 'kernel/p105/build'
        producer.unlink()
        producer.symlink_to('/tmp/missing-producer')
        result = subprocess.run([str(self.root / 'kernel/build'), 'test-device', '--help'],
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Invalid platform producer path', result.stderr)


if __name__ == '__main__':
    unittest.main()

"""The cache helper must ship in the wheel, both roots and integrity coverage."""
import hashlib
from pathlib import Path
import tempfile
import unittest

from orchard_hardware.image import runtime


class CacheRuntimeStaging(unittest.TestCase):
    def test_public_runtime_stages_and_hashes_the_cache_helper(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            modules = root / 'lib/modules'
            release = modules / 'test-kernel'
            release.mkdir(parents=True)
            (root / 'kernel.release').write_text('test-kernel\n')
            (release / 'test-module.ko').write_bytes(b'public module fixture')
            policy = root / 'policy'
            policy.mkdir()
            (policy / 'image_policy.py').write_text('# public image policy\n')
            _, files = runtime.files(modules, policy)
            name = 'usr/libexec/orchard-j71b/root_cache.py'
            data, mode = files[name]
            self.assertIn(b'class Cache:', data)
            self.assertEqual(mode, 0o755)
            expected = hashlib.sha256(data).hexdigest() + '  /' + name + '\n'
            self.assertIn(expected, files['etc/orchard/public.SHA256SUMS'][0].decode())


if __name__ == '__main__':
    unittest.main()

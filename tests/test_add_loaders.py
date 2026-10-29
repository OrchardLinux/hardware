"""The published wheel gains the launch loader without being built twice."""
import base64
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('add_loaders', ROOT / 'ci/add_loaders.py')
add_loaders = importlib.util.module_from_spec(spec)
spec.loader.exec_module(add_loaders)


class AddLoaders(unittest.TestCase):
    def test_files_and_record_hashes_are_added(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheel = root / 'orchard_hardware-1-py3-none-any.whl'
            with zipfile.ZipFile(wheel, 'w') as archive:
                archive.writestr('orchard_hardware/__init__.py', 'x')
                archive.writestr('orchard_hardware-1.dist-info/RECORD',
                                 'orchard_hardware/__init__.py,sha256=abc,1\norchard_hardware-1.dist-info/RECORD,,\n')
            (root / 'm1n1.bin').write_bytes(b'm1n1')
            (root / 'pongo.bin').write_bytes(b'pongo')
            add_loaders.add(wheel, {'orchard_hardware/loaders/m1n1.bin': root / 'm1n1.bin',
                                    'orchard_hardware/loaders/pongo.bin': root / 'pongo.bin'})
            with zipfile.ZipFile(wheel) as archive:
                self.assertEqual(archive.read('orchard_hardware/loaders/pongo.bin'), b'pongo')
                record = archive.read('orchard_hardware-1.dist-info/RECORD').decode().splitlines()
            digest = base64.urlsafe_b64encode(hashlib.sha256(b'm1n1').digest()).rstrip(b'=').decode()
            self.assertIn(f'orchard_hardware/loaders/m1n1.bin,sha256={digest},4', record)
            self.assertEqual(record[-1], 'orchard_hardware-1.dist-info/RECORD,,')
            with self.assertRaisesRegex(ValueError, 'already has'):
                add_loaders.add(wheel, {'orchard_hardware/loaders/m1n1.bin': root / 'm1n1.bin'})


if __name__ == '__main__':
    unittest.main()

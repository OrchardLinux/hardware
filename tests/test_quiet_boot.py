"""Shared discovery must not accidentally inherit distro artwork."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from orchard_hardware.image.launcher import verify_quiet_boot
import orchard_hardware


class QuietBoot(unittest.TestCase):
    def test_receipt_binds_both_components_and_current_patches(self):
        here = Path(orchard_hardware.__file__).parent / 'boot/quiet'
        files = {role: {'bytes': 1, 'sha256': role * 8} for role in ('loader', 'pongo')}
        receipt = {'schema': 1, 'profile': 'j71b', 'presentation': 'black',
                   'files': files, 'patches': {
                       name: hashlib.sha256((here / name).read_bytes()).hexdigest()
                       for name in ('pongo.patch', 'm1n1.patch')}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quiet.json'
            path.write_text(json.dumps(receipt))
            verify_quiet_boot(path, {'files': files})
            for role in files:
                changed = {**files, role: {'bytes': 1, 'sha256': 'different'}}
                with self.assertRaisesRegex(ValueError, 'quiet ' + role):
                    verify_quiet_boot(path, {'files': changed})
            receipt['presentation'] = 'alpine'
            path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'black-screen'):
                verify_quiet_boot(path, {'files': files})
            receipt['presentation'] = 'black'
            receipt['patches']['pongo.patch'] = 'outdated'
            path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'provenance'):
                verify_quiet_boot(path, {'files': files})


if __name__ == '__main__':
    unittest.main()

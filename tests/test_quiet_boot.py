"""Shared discovery must not accidentally inherit distro artwork."""
import hashlib
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
import tempfile
import unittest

from orchard_hardware.image.launcher import verify_quiet_boot
import orchard_hardware


class QuietBoot(unittest.TestCase):
    def test_published_launcher_requires_paired_cli(self):
        workflow = (Path(__file__).resolve().parents[1] / '.github/workflows/launcher.yml').read_text()
        script = textwrap.dedent(workflow.split("          python - <<'PY'\n", 1)[1].split('          PY\n', 1)[0])
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'out/launcher'
            output.mkdir(parents=True)
            for name in ('boot.toml', 'j71b-launcher.boot.toml'):
                (output / name).write_text('min_orchard = "0.1.8"\nprotocol = 1\n')
            subprocess.run([sys.executable, '-c', script], cwd=directory,
                           env={**os.environ, 'CLI_TAG': 'v0.1.10'}, check=True)
            for path in output.iterdir():
                self.assertEqual(path.read_text(), 'min_orchard = "0.1.10"\nprotocol = 1\n')

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

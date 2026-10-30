"""Shared infrastructure must work without importing board-specific support."""
import hashlib
import orchard_cli
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard_cli import firmware
from orchard_cli.apple import usb
from orchard_cli.progress import Clock
from orchard_cli.state import DeviceState

FIXTURE = Path(__file__).parent / 'fixtures/first/index.toml'


class PackageBoundaries(unittest.TestCase):
    def test_removed_board_local_modules_are_not_shipped(self):
        package=Path(orchard_cli.__file__).parent
        self.assertFalse((package/'apple/adt.py').exists())
        # Devices are hardware data; the CLI ships no per-device package.
        self.assertFalse((package/'j71b').exists())

    def test_shared_imports_and_offline_commands_do_not_import_j71b(self):
        script = '''
import importlib.abc, os, sys
class NoBoard(importlib.abc.MetaPathFinder):
 def find_spec(self, name, path=None, target=None):
  if name == 'orchard_cli.j71b' or name.startswith('orchard_cli.j71b.'):
   raise AssertionError('Offline/common code imported board: '+name)
sys.meta_path.insert(0, NoBoard())
from orchard_cli import downloads, firmware, http, integrity
from orchard_cli.transports import ssh
from orchard_cli.apple import adt_tree, adt_capture, macho, osserialize, remote_ipsw, udif, apfs, lzfse, usb, boot
from orchard_hardware.apple import adt
from orchard_cli.command import main
sys.argv=['orchard','distros'];main()
sys.argv=['orchard','resolve',MANIFEST,'--profile','j71b'];main()
'''.replace('MANIFEST', repr(str(FIXTURE.resolve())))
        with tempfile.TemporaryDirectory() as store:
            result = subprocess.run([sys.executable, '-I', '-c', script], cwd=store,
                env={**os.environ, 'ORCHARD_HOME':store}, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('example-1', result.stdout)

    def test_state_rejects_non_profile_paths(self):
        for profile in ('../other', '/tmp/other', '', 'a/b'):
            with self.assertRaisesRegex(ValueError, 'Invalid device profile'):
                DeviceState(profile, 1)

    def test_apple_usb_selector_uses_explicit_identity_constraints(self):
        device = {'location':'port','mode':'pongo','ecid':1,'chip':2,'board':3}
        with patch.object(usb, 'devices', return_value=[device]):
            self.assertEqual(usb.selected('port',1,chip=2,board=3), device)
            for ecid, chip, board in ((9,2,3),(1,9,3),(1,2,9)):
                with self.assertRaisesRegex(ValueError, 'Wrong device identity'):
                    usb.selected('port',ecid,chip=chip,board=board)

    def test_apple_recovery_selection_is_product_scoped_and_unambiguous(self):
        device = {'location':'port','mode':'linux','product':'Another Recovery','serial_port':'serial',
                  'ecid':1, 'boot_id':'11111111-1111-4111-8111-111111111111'}
        with patch.object(usb, 'devices', return_value=[device]):
            self.assertEqual(usb.recovery('Another Recovery'), device)
            with self.assertRaises(ValueError):usb.recovery('J71b Recovery')
        with patch.object(usb, 'devices', return_value=[device,{**device,'location':'other'}]):
            with self.assertRaises(ValueError):usb.recovery('Another Recovery')

    def test_adt_capture_keeps_its_model_gate(self):
        from orchard_cli import devices
        from orchard_cli.apple import device as adapter_module
        adapter = devices.load('j71b')
        with patch.object(adapter_module.adt_capture,'capture',return_value=b'fresh'), \
             patch.object(adapter_module,'parse',return_value=Mock(properties={'model':b'different\0'})):
            with self.assertRaisesRegex(ValueError,'Captured ADT is not for'):
                adapter.capture_adt()

    def test_shared_mixin_packer_rejects_traversal_and_links_before_archiving(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('lib/firmware/../../secret','/etc/shadow','lib/firmware//data'):
                with self.assertRaises(ValueError):
                    firmware.pack_mixin(root,{'files':{name:{}}},root)
                self.assertFalse((root/'mixin.tar.gz').exists())
            (root/'firmware/lib/firmware').mkdir(parents=True)
            (root/'secret').write_text('private')
            (root/'firmware/lib/firmware/data').symlink_to(root/'secret')
            with self.assertRaisesRegex(ValueError,'Linked'):
                firmware.pack_mixin(root,{'files':{'lib/firmware/data':{}}},root)
            self.assertFalse((root/'mixin.tar.gz').exists())

    def test_mixin_packaging_is_data_only_and_profile_scoped(self):
        import tarfile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);name='lib/firmware/example.bin'
            path=root/'firmware'/name;path.parent.mkdir(parents=True);path.write_bytes(b'firmware')
            metadata={'schema':1,'profile':'another-board','boot_nonce':'not-an-adt',
                      'files':{name:{'bytes':8,'sha256':hashlib.sha256(b'firmware').hexdigest()}}}
            archive, result = firmware.pack_mixin(root,metadata,root)
            with tarfile.open(archive) as bundle:
                self.assertEqual(json.load(bundle.extractfile('mixin.json'))['profile'],'another-board')
                self.assertEqual(bundle.extractfile(name).read(),b'firmware')
                self.assertTrue(all(m.mode==0o600 and m.isfile() for m in bundle))
            self.assertEqual(result['sha256'],hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(archive.stat().st_mode & 0o777,0o600)


if __name__ == '__main__':unittest.main()

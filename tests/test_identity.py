"""Boot identity publication from bounded ADT fixtures; no device activation."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from orchard_hardware.apple import adt

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('orchard_identity_test', ROOT/'runtime/j71b/identity.py')
identity = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'adt':adt}):
    spec.loader.exec_module(identity)
BOOT = '11111111-1111-4111-8111-111111111111'


def node(name, properties=(), children=()):
    entries = [('name',name.encode()+b'\0'), *properties]
    result = struct.pack('<II',len(entries),len(children))
    for key,value in entries:
        result += key.encode().ljust(32,b'\0') + struct.pack('<I',len(value)) + value + b'\0'*(-len(value)%4)
    return result + b''.join(children)


class Identity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name,value in (('RUN',self.root/'run'),('DEV',self.root),('MTD',self.root/'mtd'),
                           ('BOOT_ID',self.root/'boot-id')):
            patched=patch.object(identity,name,value)
            patched.start()
            self.addCleanup(patched.stop)
        identity.BOOT_ID.write_text(BOOT)
        blob=node('device-tree',children=[node('chosen',[('unique-chip-id',(123).to_bytes(8,'little'))])])
        identity.MTD.write_text(f'dev: size erasesize name\nmtd0: {len(blob):08x} 00001000 "adt"\n')
        (self.root/'mtd0').write_bytes(blob)

    def owner(self, path):
        result = self.original_lstat(path)
        if path.name == 'identity.json':
            values=list(result)
            values[4]=0  # Fixtures may run as a non-root macOS user.
            result=os.stat_result(values)
        return result

    def publish_fixture(self):
        with patch.object(identity.stat,'S_ISCHR',return_value=True):
            return identity.initialize()

    def test_published_record_matches_live_adt_and_is_boot_bound_and_private(self):
        self.original_lstat=Path.lstat
        with patch.object(Path,'lstat',lambda path: self.owner(path)):
            record=self.publish_fixture()
            self.assertEqual(record,{'profile':'j71b','ecid':123,'boot_id':BOOT})
            self.assertEqual(identity.cached(),record)
            self.assertEqual(stat.S_IMODE((identity.RUN/'identity.json').stat().st_mode),0o600)
            with self.assertRaisesRegex(ValueError,'already published'):
                identity.initialize()
            identity.BOOT_ID.write_text('22222222-2222-4222-8222-222222222222')
            with self.assertRaisesRegex(ValueError,'differs from this boot'):
                identity.cached()

    def test_missing_duplicate_and_truncated_adt_never_publish(self):
        original=identity.MTD.read_text()
        for table in ('',original+original):
            identity.MTD.write_text(table)
            with self.assertRaisesRegex(ValueError,'one live ADT'):
                self.publish_fixture()
            self.assertFalse((identity.RUN/'identity.json').exists())
        identity.MTD.write_text(original)
        (self.root/'mtd0').write_bytes(b'short')
        with self.assertRaisesRegex(ValueError,'truncated'):
            self.publish_fixture()
        self.assertFalse((identity.RUN/'identity.json').exists())

    def test_unprotected_or_linked_record_is_refused(self):
        identity.RUN.mkdir()
        path=identity.RUN/'identity.json'
        path.write_text('{}')
        path.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'not protected'):
            identity.cached()
        path.unlink()
        path.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(ValueError,'not protected'):
            identity.cached()

    def test_gadget_publishes_identity_before_binding_usb(self):
        source=(ROOT/'boot/initramfs/j71b/recovery/usb-gadget').read_text()
        self.assertLess(source.index('identity.py'),source.index('/serialnumber'))
        self.assertLess(source.index('/serialnumber'),source.index('"$g/UDC"'))


if __name__ == '__main__':
    unittest.main()

"""P105 recovery identity, flash fence and one-shot NFS request validation."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SPEC=importlib.util.spec_from_file_location('p105_device_test',Path(__file__).resolve().parents[1]/'runtime/p105/recovery/device.py')
device=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(device)
BOOT='11111111-1111-4111-8111-111111111111'


class Recovery(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.run=root/'run';self.run.mkdir()
        self.mtd=root/'mtd';self.mtd.mkdir()
        self.compatible=root/'compatible';self.compatible.write_bytes(b'apple,p105ap\0')
        self.flash=root/'flash';self.flash.write_bytes(b'disabled\0')
        self.boot=root/'boot';self.boot.write_text(BOOT)
        self.config=root/'config';self.config.write_text(json.dumps({'profile':'p105','ecid':1,'release':'test'}))
        for name,value in {'RUN':self.run,'MTD':self.mtd,'COMPATIBLE':self.compatible,'FLASH':self.flash,
                           'BOOT_ID':self.boot,'CONFIG':self.config}.items():
            p=patch.object(device,name,value);p.start();self.addCleanup(p.stop)

    def request(self,**values):
        return {'profile':'p105','ecid':1,'boot_id':BOOT,'operation_id':BOOT,
                'nfs_root':'10.55.0.1:/root',**values}

    def test_identity_uses_the_live_kernel_boot_id(self):
        self.assertEqual(device.identity(),{'profile':'p105','ecid':1,'boot_id':BOOT})
        new='22222222-2222-4222-8222-222222222222';self.boot.write_text(new)
        self.assertEqual(device.identity()['boot_id'],new)

    def test_enabled_flash_or_any_mtd_device_blocks_operations(self):
        self.flash.write_bytes(b'okay\0')
        with self.assertRaisesRegex(ValueError,'fence'):device.identity()
        self.flash.write_bytes(b'disabled\0');(self.mtd/'mtd0').mkdir()
        with self.assertRaisesRegex(ValueError,'fence'):device.identity()

    def test_wrong_board_blocks_identity(self):
        self.compatible.write_bytes(b'apple,j71b\0')
        with self.assertRaisesRegex(ValueError,'P105'):device.identity()

    def test_handoff_rejects_wrong_device_and_boot_before_writing(self):
        for change in ({'ecid':2},{'boot_id':'22222222-2222-4222-8222-222222222222'},{'ecid':True}):
            with self.subTest(change=change),self.assertRaises(ValueError):device.handoff(self.request(**change))
        self.assertFalse((self.run/'handoff.json').exists())

    def test_handoff_is_bound_and_accepted_once(self):
        with patch.object(device,'root_type',return_value='rootfs'):
            result=device.handoff(self.request())
            self.assertEqual(result['status'],'accepted')
            self.assertEqual(result['ecid'],1)
            with self.assertRaises(FileExistsError):device.handoff(self.request())

    def test_nfs_status_exposes_no_storage_operations(self):
        with patch.object(device,'root_type',return_value='rootfs'):
            result=device.status({})
        self.assertEqual(result['operations'],['status','diagnostics','handoff'])
        self.assertEqual(result['capabilities'],['status','diagnostics','handoff-nfs'])
        self.assertFalse(result['ready'])

    def test_mount_transition_stays_pending_until_live_fence_returns(self):
        live=device.identity()
        (self.run/'identity.json').write_text(json.dumps(live))
        (self.run/'handoff.json').write_text(json.dumps({**live,'nfs_root':'10.55.0.1:/root'}))
        with patch.object(device,'identity',side_effect=FileNotFoundError(2,'moving',str(self.compatible))):
            result=device.status({})
        self.assertEqual(result['mode'],'handoff')
        self.assertFalse(result['ready'])
        self.assertNotIn('handoff-nfs',result['capabilities'])


if __name__ == '__main__':unittest.main()

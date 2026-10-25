"""Launch authorization tests; no physical devices, writes or fake readiness."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

RUNTIME=Path(__file__).resolve().parents[1]/'runtime/j71b'
spec=importlib.util.spec_from_file_location('launch_test',RUNTIME/'launch.py')
launch=importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class LaunchValidation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'minimal'
        (self.root/'etc/orchard').mkdir(parents=True)
        (self.root/'usr/bin').mkdir(parents=True)
        (self.root/'usr/sbin').mkdir()
        (self.root/'sbin').symlink_to('usr/sbin')
        (self.root/'usr/sbin/init').symlink_to('/bin/busybox')
        (self.root/'bin').symlink_to('usr/bin')
        (self.root/'usr/bin/busybox').write_bytes(b'fixture init')
        (self.root/'usr/bin/busybox').chmod(0o755)
        self.receipt={'schema':1,'release':'fixture','root_sha256':'a'*64,
                      'partition':'p','filesystem_uuid':'fs','directory':'minimal'}
        (self.root/'.orchard-install.json').write_text(json.dumps(self.receipt))
        (self.root/'etc/orchard/release.json').write_text(json.dumps({'release':'fixture','kernel_release':'kernel'}))
        self.record={'release':'fixture','root_sha256':'a'*64,'kernel_release':'kernel',
                     'init':'/sbin/init','init_target':'usr/bin/busybox',
                     'files':{p:launch.sha(self.root/p) for p in ('usr/bin/busybox','etc/orchard/release.json')}}
        (self.root/'etc/orchard/public.SHA256SUMS').write_text('')
        (self.root/'usr/share/ipadlinux').mkdir(parents=True)
        (self.root/'usr/share/ipadlinux/hardware.SHA256SUMS').write_text('')
        self.mixin=Path(self.temp.name)/'mixin.json'
        self.mixin.write_text(json.dumps({'files':{}}))
        self.catalogue=Path(self.temp.name)/'launch.json'
        self.catalogue.write_text(json.dumps({'installations':[self.record]}))
        for patcher in (patch.object(launch,'CATALOGUE',self.catalogue),
                        patch.object(launch,'MIXIN',self.mixin),
                        patch.object(launch.os,'uname',return_value=types.SimpleNamespace(release='kernel')),
                        patch.object(launch,'regular',side_effect=lambda root,name:root/name)):
            patcher.start(); self.addCleanup(patcher.stop)
        self.partition={'id':'p','filesystem_uuid':'fs'}

    def test_image_owned_init_and_receipt_are_validated_without_executing_policy(self):
        self.assertEqual(launch.validate(self.root,self.partition),(self.receipt,'/sbin/init'))
        self.assertEqual(launch.init_target(self.root,'/sbin/init'),'usr/bin/busybox')

    def test_unsupported_receipt_changed_files_and_wrong_partition_are_refused(self):
        with self.assertRaisesRegex(ValueError,'partition/filesystem'):
            launch.validate(self.root,{'id':'other','filesystem_uuid':'fs'})
        with patch.object(launch.os,'uname',return_value=types.SimpleNamespace(release='other')):
            with self.assertRaisesRegex(ValueError,'running kernel'):
                launch.validate(self.root,self.partition)
        (self.root/'usr/bin/busybox').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'integrity differs'):
            launch.validate(self.root,self.partition)
        self.receipt['root_sha256']='b'*64
        (self.root/'.orchard-install.json').write_text(json.dumps(self.receipt))
        with self.assertRaisesRegex(ValueError,'no explicit compatible'):
            launch.validate(self.root,self.partition)

    def test_installed_firmware_must_match_fresh_device_bound_preparation(self):
        firmware=self.root/'usr/lib/firmware/apple/private.bin'
        firmware.parent.mkdir(parents=True);firmware.write_bytes(b'other device')
        self.mixin.write_text(json.dumps({'files':{'lib/firmware/apple/private.bin':
            {'bytes':5,'sha256':'a'*64}}}))
        with self.assertRaisesRegex(ValueError,'private firmware differs'):
            launch.validate(self.root,self.partition)

    def test_init_link_escape_and_cycles_are_refused(self):
        path=self.root/'usr/sbin/init';path.unlink();path.symlink_to('../../../escape')
        with self.assertRaisesRegex(ValueError,'escapes root'):
            launch.init_target(self.root,'/sbin/init')
        path.unlink();path.symlink_to('/sbin/init')
        with self.assertRaisesRegex(ValueError,'cycle'):
            launch.init_target(self.root,'/sbin/init')

    def test_stale_boot_missing_confirmation_and_replay_never_mount(self):
        run=Path(self.temp.name)/'run';run.mkdir()
        snapshot={'boot_id':'boot','selection_token':'token','installations':[
            {'target':'p','directory':'minimal','receipt':self.receipt,'init':'/sbin/init'}]}
        (run/'launch-targets.json').write_text(json.dumps(snapshot))
        device=types.SimpleNamespace(RUN=run,config=lambda:{'mode':'launcher'},
            state=lambda:{'phase':'prepare','status':'complete'},mount_target=Mock())
        request={'boot_id':'boot','selection_token':'token','target':'p','directory':'minimal',
                 'confirmation':'launch minimal'}
        with patch.dict(sys.modules,device=device):
            for change in ({'boot_id':'old'},{'selection_token':'old'},{'confirmation':''},{'target':'other'}):
                with self.assertRaises(ValueError): launch.launch({**request,**change})
            device.state=lambda:{'phase':'launch','status':'running'}
            with self.assertRaises(ValueError): launch.launch(request)
        device.mount_target.assert_not_called()


class StrictFiles(unittest.TestCase):
    def test_linked_and_writable_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'file').write_text('data');(root/'file').chmod(0o666)
            (root/'link').symlink_to('file')
            for name in ('file','link','../outside'):
                with self.assertRaises(ValueError): launch.regular(root,name)

    def test_replay_validation_failure_restores_fence_and_never_switches_root(self):
        policy=types.SimpleNamespace(INIT='/sbin/init',validate_handoff=Mock())
        with patch.dict(sys.modules,image_policy=policy):
            spec=importlib.util.spec_from_file_location('handoff_test',RUNTIME/'handoff.py')
            handoff=importlib.util.module_from_spec(spec);spec.loader.exec_module(handoff)
        fake=types.SimpleNamespace(validate=Mock(side_effect=ValueError('journal changed')))
        with patch.dict(sys.modules,launch=fake),patch.object(handoff,'run') as run:
            with self.assertRaisesRegex(ValueError,'journal changed'):
                handoff.writable_root(Path('/owned'),Path('/owned/root'),{'path':'/dev/owned'},
                                      {'launch':True,'receipt':{},'init':'/sbin/init'})
            self.assertEqual(run.call_args_list[-2].args,('umount','/owned'))
            self.assertEqual(run.call_args_list[-1].args,('blockdev','--setro','/dev/owned'))


if __name__=='__main__': unittest.main()

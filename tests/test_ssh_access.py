import importlib.util
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('ssh_access_test', Path(__file__).resolve().parents[1] / 'runtime/j71b/ssh_access.py')
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class SSHAccess(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for name in ('RUN', 'SESSION', 'AUTHORIZED', 'BOOT_ID', 'PROC'):
            p = patch.object(access, name, self.root / name)
            p.start();self.addCleanup(p.stop)
        access.RUN.mkdir();access.SESSION.mkdir();access.PROC.mkdir()
        access.BOOT_ID.write_text('boot\n')
        (access.SESSION/'authorized_keys').write_text('public key')
        os.link(access.SESSION/'authorized_keys', access.AUTHORIZED)
        for name in ('ssh_host_ed25519_key', 'ssh_host_ed25519_key.pub'):
            (access.SESSION/name).write_text('key')
        self.binary = self.root/'dropbear'
        self.binary.touch()

    def test_marker_requires_current_boot_release_private_root_owned_regular_file(self):
        access.write_marker('ssh-revoked.json', 'release')
        path = access.RUN/'ssh-revoked.json'
        with patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_mode=0o100600, st_uid=0)):
            self.assertTrue(access.revoked('release'))
            self.assertFalse(access.revoked('different'))
            access.BOOT_ID.write_text('different-boot')
            self.assertFalse(access.revoked('release'))
        for mode, uid in ((0o120600,0),(0o100644,0),(0o100600,1000)):
            with patch.object(Path, 'lstat', return_value=types.SimpleNamespace(st_mode=mode, st_uid=uid)):
                self.assertFalse(access.revoked('release'))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_retirement_empties_auth_before_stopping_and_keeps_only_receipt_connection(self):
        original = Path.read_text
        def read(path, *args, **kwargs):
            if str(path) == '/proc/self/mountinfo':return f'1 2 0:0 / {access.AUTHORIZED} rw - tmpfs tmpfs rw\n'
            return original(path, *args, **kwargs)
        def stop():self.assertEqual((access.SESSION/'authorized_keys').read_text(), '')
        process = access.PROC/'13'
        process.mkdir();(process/'exe').symlink_to(self.binary.resolve())
        with patch.object(access, 'revoked', return_value=False), \
             patch.object(access, 'connection_pid', return_value=12), \
             patch.object(Path, 'read_text', read), \
             patch.object(access, 'server_pids', side_effect=[{12,13},{12}]), \
             patch.object(access.os, 'pidfd_open', return_value=99, create=True) as open_pid, \
             patch.object(access.os, 'close') as close, \
             patch.object(access.signal, 'pidfd_send_signal', create=True) as kill, \
             patch.object(access.subprocess, 'run') as unmount:
            # Do not mock close for real auth fd: retain actual close behavior.
            close.side_effect = lambda fd: None if fd == 99 else os_close(fd)
            result = access.revoke('release', str(self.binary), stop)
        self.assertEqual(result, {'ssh_access':'revoked','release':'release','boot_id':'boot'})
        open_pid.assert_called_once_with(13)
        kill.assert_called_once()
        unmount.assert_called_once()
        self.assertFalse(access.AUTHORIZED.exists())
        self.assertEqual(list(access.SESSION.iterdir()), [])
        self.assertTrue((access.RUN/'ssh-revoked.json').is_file())

    def test_persisted_authorization_copy_is_rejected_before_teardown(self):
        access.AUTHORIZED.unlink();access.AUTHORIZED.write_text('public key')
        stop = Mock()
        with patch.object(access,'revoked',return_value=False), patch.object(access,'connection_pid',return_value=12):
            with self.assertRaisesRegex(ValueError,'RAM-backed'):
                access.revoke('release',str(self.binary),stop)
        stop.assert_not_called()
        self.assertEqual((access.SESSION/'authorized_keys').read_text(),'public key')


os_close = os.close
if __name__ == '__main__':unittest.main()

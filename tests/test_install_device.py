"""install_device.py: the storage fence, and writing orchard's platform layer into a new root."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchard import install_device as D
from orchard.image import platform


class StorageFence(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        for name, path in (('STORAGE_FENCE', 'storage-fence'), ('STORAGE_READY', 'storage-ready'),
                           ('STORAGE_FAILED', 'storage-failed')):
            patcher = patch.object(D, name, self.root / path)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_no_wait_outside_the_recovery(self):
        D.wait_for_storage(seconds=0)

    def test_ready_marker_ends_the_wait(self):
        (self.root / 'storage-fence').touch()
        (self.root / 'storage-ready').touch()
        D.wait_for_storage(seconds=0)

    def test_failure_or_timeout_is_storage_unavailable(self):
        (self.root / 'storage-fence').touch()
        with self.assertRaisesRegex(D.InstallError, 'not ready after 0 s') as caught:
            D.wait_for_storage(seconds=0)
        self.assertEqual(caught.exception.code, 'storage-unavailable')
        (self.root / 'storage-failed').write_text('nvme0 never became live\n')
        with self.assertRaisesRegex(D.InstallError, 'nvme0 never became live'):
            D.wait_for_storage(seconds=5)


class PlatformLayer(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.root = self.work / 'root'
        (self.root / 'usr/lib/firmware').mkdir(parents=True)
        (self.root / 'usr/lib/firmware/brcm.bin').write_bytes(b'distro firmware')
        patcher = patch.object(D.os, 'fchown')  # tests do not run as root
        patcher.start()
        self.addCleanup(patcher.stop)

    def layer(self, files):
        path = self.work / 'layer.tar.gz'
        path.unlink(missing_ok=True)
        expanded, _ = platform.write({name: (data, 0o644) for name, data in files.items()}, path)
        data = path.read_bytes()
        return path, {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'expanded_limit': expanded}

    def test_the_layer_lands_next_to_the_root_image(self):
        path, receipt = self.layer({'usr/lib/modules/6.18-orchard/vmlinuz': b'kernel',
                                    'usr/lib/modules/6.18-orchard/dtb/t8010-j71b.dtb': b'dtb',
                                    'usr/lib/firmware/regulatory.db': b'regdb'})
        self.assertEqual(D.install_layer(path, self.root, **receipt), 3)
        self.assertEqual((self.root / 'usr/lib/modules/6.18-orchard/vmlinuz').read_bytes(), b'kernel')
        self.assertEqual((self.root / 'usr/lib/firmware/brcm.bin').read_bytes(), b'distro firmware')
        self.assertEqual(D.kernel_version(self.root), '6.18-orchard')

    def test_a_file_the_root_image_already_has_is_refused(self):
        path, receipt = self.layer({'usr/lib/firmware/brcm.bin': b'other'})
        with self.assertRaisesRegex(D.InstallError, 'already has /usr/lib/firmware/brcm.bin'):
            D.install_layer(path, self.root, **receipt)
        self.assertEqual((self.root / 'usr/lib/firmware/brcm.bin').read_bytes(), b'distro firmware')

    def test_only_files_under_usr_lib_and_only_the_planned_bytes(self):
        path, receipt = self.layer({'etc/passwd': b'root::0:0::/:/bin/sh'})
        with self.assertRaisesRegex(D.InstallError, 'only files under /usr/lib'):
            D.install_layer(path, self.root, **receipt)
        path, receipt = self.layer({'usr/lib/modules/x/vmlinuz': b'kernel'})
        with self.assertRaises(D.InstallError) as caught:
            D.install_layer(path, self.root, **{**receipt, 'sha256': '0' * 64})
        self.assertEqual(caught.exception.code, 'layer-mismatch')
        with self.assertRaises(D.InstallError) as caught:
            D.install_layer(path, self.root, **{**receipt, 'expanded_limit': 1})
        self.assertEqual(caught.exception.code, 'stream-too-long')


if __name__ == '__main__':
    unittest.main()

"""install_device.py waits for the recovery's storage fence before reading the disk."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from orchard_cli import install_device as D


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


if __name__ == '__main__':
    unittest.main()

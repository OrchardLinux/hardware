"""orchard shell/exec/push/pull reach an installed system as its user."""
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from orchard import computer_key, ram_session

SYSTEM = {'location': 1, 'mode': 'system', 'product': 'Omarchy A', 'serial_port': '/dev/cu.x',
          'boot_id': '00000000-0000-4000-8000-000000000001', 'profile': 'j71b'}
PAYLOAD = {**SYSTEM, 'mode': 'ram', 'product': 'Orchard gpu_001'}


class SystemSession(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = mock.patch.object(computer_key, 'directory', return_value=Path(self.temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_installed_system_is_a_target_when_no_payload_runs(self):
        with mock.patch('orchard.apple.usb.devices', return_value=[SYSTEM]):
            self.assertEqual(ram_session.target()['mode'], 'system')
        with mock.patch('orchard.apple.usb.devices', return_value=[SYSTEM, PAYLOAD]):
            self.assertEqual(ram_session.target()['mode'], 'ram')

    def test_it_signs_in_as_the_user_its_install_allowed(self):
        computer_key.remember_user('tablet')
        with mock.patch.object(computer_key, 'identity', return_value=Path('/key')), \
                mock.patch.object(ram_session, 'wait_authenticated'):
            argv = ram_session.connect(SYSTEM)
        self.assertEqual(argv[-1], 'tablet@172.16.42.1')
        self.assertIn('StrictHostKeyChecking=accept-new', argv)

    def test_without_a_user_it_says_how_to_get_one(self):
        with self.assertRaisesRegex(ValueError, '--user'):
            ram_session.connect(SYSTEM)


if __name__ == '__main__':
    unittest.main()

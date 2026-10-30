"""USB port reset after checkm8: fake sysfs and palera1n logs; never touches a device."""
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from orchard_cli.apple import replug

CHECKMATE = ' - <Info>: Checkmate!\n - <Verbose>: Device should now reconnect in download mode\n' \
            ' - <Verbose>: DFU mode device disconnected\n'
RETURNED = ' - <Info>: Entered download mode\n'


class PortReset(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        port = self.root / 'usb1-port2'
        port.mkdir()
        self.control = port / 'disable'
        self.control.write_text('0')
        (self.root / '1-2').mkdir()
        (self.root / '1-2' / 'port').symlink_to('../usb1-port2')
        self.log = self.root / 'palera1n.log'
        self.log.write_text(' - <Info>: Waiting for devices\n')

    def tearDown(self):
        self.temporary.cleanup()

    def watch(self, **timing):
        watcher = replug.PortReset(self.log, self.control, **{'settle': 0.2, 'wait': 0.3, **timing})
        watcher.start()
        return watcher

    def test_port_control_requires_a_writable_disable_attribute(self):
        self.assertEqual(replug.port_control('1-2', root=self.root), self.control.resolve())
        self.assertIsNone(replug.port_control('1-9', root=self.root))
        self.assertIsNone(replug.port_control(0x14200000, root=self.root))  # macOS location
        self.control.chmod(0o444)
        if os.access(self.control, os.W_OK):
            self.skipTest('running as root: permissions are not enforced')
        self.assertIsNone(replug.port_control('1-2', root=self.root))

    def test_no_reset_when_the_device_returns_by_itself(self):
        self.control.write_text('untouched')
        self.log.write_text(CHECKMATE + RETURNED)
        watcher = self.watch()
        watcher.join(5)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(watcher.resets, 0)
        self.assertEqual(self.control.read_text(), 'untouched')

    def test_resets_until_the_device_returns_and_leaves_the_port_enabled(self):
        self.log.write_text(CHECKMATE)
        watcher = self.watch()

        def returns_after_first_reset():
            while watcher.resets < 1:
                time.sleep(0.02)
            with self.log.open('a') as log:
                log.write(RETURNED)
        helper = threading.Thread(target=returns_after_first_reset)
        helper.start()
        watcher.join(5)
        helper.join(5)
        self.assertEqual(watcher.resets, 1)
        self.assertEqual(self.control.read_text(), '0')

    def test_gives_up_after_the_attempts_and_leaves_the_port_enabled(self):
        self.log.write_text(CHECKMATE)
        watcher = self.watch(attempts=2)
        watcher.join(5)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(watcher.resets, 2)
        self.assertEqual(self.control.read_text(), '0')

    def test_stop_before_checkmate_does_nothing(self):
        self.control.write_text('untouched')
        watcher = self.watch()
        time.sleep(0.2)
        watcher.stop()
        watcher.join(5)
        self.assertFalse(watcher.is_alive())
        self.assertEqual(watcher.resets, 0)
        self.assertEqual(self.control.read_text(), 'untouched')


if __name__ == '__main__':
    unittest.main()

"""Bounded read-only USB polling; never retry boot/upload or identity failures."""
import subprocess
import unittest
from unittest.mock import patch

from orchard.apple import boot

ENDPOINT = {'mode':'linux', 'location':'linux-port', 'ecid':123,
            'boot_id':'11111111-1111-4111-8111-111111111111',
            'product':'J71b Recovery', 'serial_port':'/dev/cu.recovery'}
DEVICE = {'location':'dfu-port', 'ecid':123}


class USBReadiness(unittest.TestCase):
    def wait(self, seconds=1):
        return boot.wait_mode(DEVICE,'linux',chip=0x8010,board=0x18,product='J71b Recovery',seconds=seconds)

    def test_transient_enumeration_failure_then_real_identity_checked_success(self):
        for error in (subprocess.CalledProcessError(1,['ioreg'],stderr=b'transient registry failure'),
                      subprocess.TimeoutExpired(['ioreg'],10)):
            with self.subTest(error=type(error).__name__), \
                 patch.object(boot.usb,'devices',side_effect=[error,[ENDPOINT]]) as probe, \
                 patch.object(boot.time,'sleep'), patch.object(boot.pongo,'upload') as upload:
                self.assertEqual(self.wait(),ENDPOINT)
                self.assertEqual(probe.call_count,2)
                upload.assert_not_called()

    def test_persistent_probe_failure_expires_with_original_error_not_success(self):
        error = subprocess.CalledProcessError(1,['ioreg'],stderr=b'registry unavailable')
        with patch.object(boot.usb,'devices',side_effect=error) as probe:
            with self.assertRaisesRegex(RuntimeError,'readiness timed out.*registry unavailable'):
                self.wait(seconds=0)
            self.assertEqual(probe.call_count,1)

    def test_wrong_identity_after_probe_failure_is_not_retried(self):
        error = subprocess.CalledProcessError(1,['ioreg'])
        with patch.object(boot.usb,'devices',side_effect=[error,[{**ENDPOINT,'ecid':456}]]) as probe, \
             patch.object(boot.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'identity differs'):
                self.wait()
            self.assertEqual(probe.call_count,2)

    def test_multiple_targets_after_probe_failure_are_not_retried(self):
        error = subprocess.CalledProcessError(1,['ioreg'])
        with patch.object(boot.usb,'devices',side_effect=[error,[ENDPOINT,{**ENDPOINT,'location':'other'}]]) as probe, \
             patch.object(boot.time,'sleep'):
            with self.assertRaisesRegex(ValueError,'exactly one target'):
                self.wait()
            self.assertEqual(probe.call_count,2)


if __name__ == '__main__':
    unittest.main()

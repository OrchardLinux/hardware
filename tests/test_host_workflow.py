"""Host workflow regression tests."""
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard.contracts import Connection
from orchard.state import BootState, DeviceState
from orchard.progress import Clock, Progress
from orchard.protocol import Client, DeviceError
from orchard.service import install
from orchard.apple.bluetool import hcd_from_binary


BOOT = '11111111-1111-4111-8111-111111111111'
SCOPE = BootState(DeviceState('device', 1), BOOT)


class HostWorkflow(unittest.TestCase):
    def setUp(self):
        self.state_temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.state_temp.cleanup)
        self.state_patch = patch('orchard.state.cache_root', return_value=Path(self.state_temp.name))
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)
    def test_cancel_before_asset_access_download_or_mutation(self):
        from unittest.mock import Mock
        adapter = Mock(NAME='Supported tablet', PROFILE='device', INSTALL=True, STATUS='supported', CAVEATS=[], FEATURES={})
        with patch('orchard.service.choose', return_value=(adapter, {'mode': 'dfu'})), \
             patch('orchard.service.resolve') as assets:
            result = install(input_fn=lambda _: 'n', progress=Progress(io.StringIO()))
            self.assertTrue(result['cancelled'])
            assets.assert_not_called()
            adapter.firmware_inputs.assert_not_called()
            adapter.boot.assert_not_called()

    def test_experimental_device_shows_caveats_and_still_defaults_to_yes(self):
        from unittest.mock import Mock
        adapter = Mock(NAME='Untested tablet', PROFILE='device', INSTALL=True, STATUS='experimental',
                       FEATURES={'touch': 'missing', 'wifi': 'untested', 'display': 'working', 'cellular': 'none'},
                       CAVEATS=['Bluetooth needs its module vendor chosen per unit.'])
        prompts = []
        with patch('orchard.service.choose', return_value=(adapter, {'mode': 'dfu'})), \
             patch('orchard.service.resolve') as assets:
            output = io.StringIO()
            result = install(input_fn=lambda text: prompts.append(text) or 'n', progress=Progress(output))
        self.assertTrue(result['cancelled'])
        self.assertEqual(prompts, ['Install Linux on Untested tablet? [Y/n] '])
        text = output.getvalue()
        self.assertIn('- Not working yet: Touchscreen', text)
        self.assertIn('- Untested: Wi-Fi', text)
        self.assertNotIn('Display', text)
        self.assertNotIn('Cellular', text)
        self.assertIn('- Bluetooth needs its module vendor chosen per unit.', text)
        self.assertIn('not been tested yet, proceed at your own risk', text)
        assets.assert_not_called()

    def test_clock_excludes_input_and_download_once(self):
        with patch('orchard.progress.time.monotonic', side_effect=[0, 1, 4, 5, 7, 12]):
            clock = Clock()
            with clock.exclude('input'):
                pass
            with clock.exclude('download'):
                pass
            report = clock.result()
        self.assertEqual(report['install_seconds'], 7)
        self.assertEqual(report['elapsed_seconds'], 12)

    def test_serial_bootstrap_consumes_only_versioned_connection_data(self):
        from orchard.transports.ssh import bootstrap_reply
        row = {'protocol': 1, 'event': 'result', 'operation': 'bootstrap',
               'result': {'profile': 'device', 'ecid': 1, 'boot_id': BOOT,
                          'host': '172.16.42.1', 'port': 22, 'host_key': 'ssh-ed25519 ' + 'A' * 68}}
        self.assertEqual(bootstrap_reply(0, json.dumps(row), SCOPE)[:2], ('172.16.42.1', 22))
        row['result']['host'] = 'host; mount /dev/example'
        with self.assertRaises(ValueError):
            bootstrap_reply(0, json.dumps(row), SCOPE)

    def test_authenticated_readiness_retries_only_read_only_probes(self):
        from unittest.mock import Mock
        from orchard.transports.ssh import wait_authenticated
        results = [subprocess.CompletedProcess([], 255, b'', b'Connection refused'),
                   subprocess.CompletedProcess([], 0, b'', b'')]
        with patch('orchard.transports.ssh.subprocess.run', side_effect=results) as run, \
             patch('orchard.transports.ssh.time.sleep'):
            wait_authenticated(['ssh', '-o', 'StrictHostKeyChecking=yes', 'root@device'], Mock())
        self.assertEqual(len(run.call_args_list), 2)
        self.assertTrue(all(call.args[0][-1] == 'true' for call in run.call_args_list))
        self.assertTrue(all('StrictHostKeyChecking=yes' in call.args[0] for call in run.call_args_list))

    def test_authenticated_readiness_never_retries_rejected_pins(self):
        from unittest.mock import Mock
        from orchard.transports.ssh import wait_authenticated
        with patch('orchard.transports.ssh.subprocess.run', return_value=
                   subprocess.CompletedProcess([], 255, b'', b'Host key verification failed')) as run:
            with self.assertRaisesRegex(ValueError, 'authentication rejected'):
                wait_authenticated(['ssh', 'root@device'], Mock())
        self.assertEqual(run.call_count, 1)

    def test_terminal_progress_updates_in_place_and_logs_are_sparse(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        for interactive in (True, False):
            output = Terminal() if interactive else io.StringIO()
            with patch('orchard.progress.time.monotonic', side_effect=[0, 1, 2, 3]):
                progress = Progress(output)
                progress.transfer(1, 3)
                progress.transfer(2, 3)
                progress.transfer(3, 3)
            text = output.getvalue()
            # A finished transfer leaves no line behind.
            if interactive:
                self.assertEqual(text.count('\r'), 3)
                self.assertEqual(text.count('\n'), 0)
            else:
                self.assertNotIn('\r', text)
                self.assertEqual(text.count("\n"), 1)

    def test_recovery_detection_does_not_require_the_dfu_location(self):
        from orchard import devices
        from orchard.apple import device as adapter_module
        adapter = devices.load('j71b')
        endpoint = {'mode': 'linux', 'location': 'new-enumeration',
                    'product': 'Orchard Recovery', 'profile': 'j71b', 'serial_port': '/dev/recovery', 'ecid': 1, 'boot_id': BOOT}
        original = {'location': 'dfu-enumeration', 'ecid': 1}
        with patch.object(adapter_module.usb, 'devices', return_value=[endpoint]):
            self.assertEqual(adapter_module.apple_boot.wait_mode(original, 'linux', chip=adapter.CHIP,
                             board=adapter.BOARD, product=adapter.PRODUCT, seconds=0), endpoint)
        with patch.object(adapter_module.adt_capture, 'capture', return_value=b'fresh'), patch.object(adapter_module, 'parse', return_value=Mock(properties={'model': b'iPad7,5\0'})), \
             patch.object(adapter_module, 'validate_adt', return_value=2):
            with self.assertRaisesRegex(ValueError, 'identity differs'):
                adapter.capture_adt(original)

    def test_recovery_detection_refuses_ambiguous_or_wrong_endpoints(self):
        from orchard import devices
        from orchard.apple import device as adapter_module
        adapter = devices.load('j71b')
        endpoint = {'mode': 'linux', 'location': 'usb',
                    'product': 'Orchard Recovery', 'profile': 'j71b', 'serial_port': '/dev/recovery', 'ecid': 1, 'boot_id': BOOT}
        with patch.object(adapter_module.usb, 'devices', return_value=[endpoint, {**endpoint, 'location': 'other'}]):
            with self.assertRaisesRegex(ValueError, 'exactly one target'):
                adapter_module.apple_boot.wait_mode({'location': 'usb'}, 'linux', chip=adapter.CHIP,
                                             board=adapter.BOARD, product=adapter.PRODUCT, seconds=0)
        with patch.object(adapter_module.usb, 'devices', return_value=[{**endpoint, 'product': 'Other device'}]):
            with self.assertRaisesRegex(RuntimeError, 'observed: linux \\(Other device\\)'):
                adapter_module.apple_boot.wait_mode({'location': 'usb'}, 'linux', chip=adapter.CHIP,
                                             board=adapter.BOARD, product=adapter.PRODUCT, seconds=0)

    def test_running_recovery_can_be_identified_without_reboot(self):
        from orchard import devices
        from orchard.apple import device as adapter_module
        adapter = devices.load('j71b')
        from unittest.mock import Mock
        endpoint = {'mode': 'linux', 'location': 'usb',
                    'product': 'Orchard Recovery', 'profile': 'j71b', 'serial_port': '/dev/recovery', 'ecid': 1, 'boot_id': BOOT}
        with patch.object(adapter_module.usb, 'devices', return_value=[endpoint]), \
             patch.object(adapter_module.adt_capture, 'capture', return_value=b'fresh'), patch.object(adapter_module, 'parse', return_value=Mock(properties={'model': b'iPad7,5\0'})), \
             patch.object(adapter_module, 'validate_adt', return_value=1), \
             patch.object(adapter_module.apple_boot.pongo, 'upload') as upload:
            device = adapter.discover()
            self.assertTrue(device['resumed'])
            self.assertEqual(device['ecid'], 1)
            adapter.boot(device, {}, {}, Mock())
            upload.assert_not_called()

    def test_protocol_reports_device_errors_and_allows_only_status(self):
        program = ('import json,sys; '
                   'print(json.dumps({"protocol":1,"event":"error","message":"identity unavailable"})); '
                   'sys.exit(1)')
        client = Client(Connection([sys.executable, '-c', program], SCOPE, lambda: None), Progress(io.StringIO()))
        with self.assertRaisesRegex(DeviceError, 'identity unavailable'):
            client.call('status', timeout=5)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            client.call('install')

    def test_protocol_rejects_incompatible_responses(self):
        program = 'print(\'{"protocol":2,"event":"result","result":{}}\')'
        client = Client(Connection([sys.executable, '-c', program], SCOPE, lambda: None), Progress(io.StringIO()))
        with self.assertRaisesRegex(DeviceError, 'Incompatible protocol'):
            client.call('status', timeout=5)

    def test_hcd_extraction_requires_the_pinned_bytes(self):
        command = b'\x4c\xfc\x0c' + b'\x00' * 4 + b'BRCMcfgS' + b'\x4e\xfc\x00'
        expected = hashlib.sha256(command).hexdigest()
        self.assertEqual(hcd_from_binary(b'prefix' + command + b'suffix', expected), command)
        with self.assertRaises(ValueError):
            hcd_from_binary(command, '0' * 64)

    def test_hcd_extraction_finds_streams_without_a_config_header(self):
        # Older BlueTool streams (BCM4350) follow each other without BRCMcfgS.
        first = b'\x4c\xfc\x06' + b'\x01' * 6 + b'\x4e\xfc\x04' + b'\xff' * 4
        second = b'\x4c\xfc\x05' + b'\x02' * 5 + b'\x4c\xfc\x02ab' + b'\x4e\xfc\x04' + b'\xff' * 4
        data = b'code' + first + b'\x00' + second + b'tail'
        self.assertEqual(hcd_from_binary(data, hashlib.sha256(second).hexdigest()), second)


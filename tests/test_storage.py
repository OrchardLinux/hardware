"""Storage safety/one-shot guards; no physical devices or hardware readiness."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime/j71b'
spec = importlib.util.spec_from_file_location('orchard_storage_test', RUNTIME / 'storage.py')
storage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(storage)


class Probing(unittest.TestCase):
    def test_no_signature_is_not_an_ext_filesystem(self):
        for code, output, expected in ((2, b'', 'unformatted'),
                                       (2, b'PART_ENTRY_UUID=partition\n', 'unformatted'),
                                       (0, b'DEVNAME=/dev/test\nPART_ENTRY_SCHEME=gpt\n', 'unformatted'),
                                       (0, b'TYPE=ext4\nUUID=value\n', 'ext4'),
                                       (0, b'TYPE=ext3\n', 'ext3'),
                                       (0, b'TYPE=crypto_LUKS\n', 'crypto_LUKS'),
                                       (0, b'PTTYPE=gpt\n', 'partition-table')):
            with self.subTest(expected=expected), patch.object(storage, 'run', return_value=
                    subprocess.CompletedProcess([], code, output, b'')):
                self.assertEqual(storage.probe(Path('/unused'))[0], expected)

    def test_ambiguous_error_and_partial_results_are_refused(self):
        for code, output, error in ((8, b'TYPE=ext4\n', b''), (1, b'', b''),
                                    (0, b'TYPE=ext4\n', b'warning'),
                                    (2, b'UUID=unknown\n', b''),
                                    (0, b'TYPE=ext4\nTYPE=ext3\n', b''), (0, b'', b'')):
            with self.subTest(code=code, output=output), patch.object(storage, 'run', return_value=
                    subprocess.CompletedProcess([], code, output, error)):
                with self.assertRaises(ValueError):
                    storage.probe(Path('/unused'))

    def test_existing_or_changed_target_never_opens_a_write_window(self):
        for kind, observed in (('ext4', {}), ('unformatted', {'different': True})):
            partition = {'filesystem': kind, 'path': '/dev/not-a-target'}
            with patch.object(storage, 'fenced'), patch.object(storage, 'record', return_value=observed), \
                 patch.object(storage.os, 'open') as opened:
                with self.assertRaisesRegex(ValueError, 'changed or already'):
                    storage.format_ext4(partition, '11111111-1111-4111-8111-111111111111')
                opened.assert_not_called()


class GPT(unittest.TestCase):
    def test_invalid_geometry_overlap_and_fallback_warnings_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            parent = root / storage.DISK.name
            (parent / 'queue').mkdir(parents=True)
            (parent / 'size').write_text('131072')
            (parent / 'queue/logical_block_size').write_text('512')
            row = {'uuid': '11111111-1111-4111-8111-111111111111', 'type': storage.LINUX_DATA,
                   'start': 2048, 'size': 65536, 'node': str(storage.DISK) + 'p1'}
            layout = {'id': '22222222-2222-4222-8222-222222222222', 'label': 'gpt',
                      'device': str(storage.DISK), 'unit': 'sectors', 'sectorsize': 512,
                      'firstlba': 34, 'lastlba': 131038, 'partitions': [row]}
            bad = ({'label': 'dos'}, {'sectorsize': 4096},
                   {'partitions': [row, {**row, 'node': str(storage.DISK) + 'p2'}]},
                   {'partitions': [{**row, 'start': 1}]},
                   {'partitions': [{**row, 'size': 131072}]}, {'id': '0' * 32})
            with patch.object(storage, 'SYS', root):
                for change in bad:
                    response = subprocess.CompletedProcess([], 0, json.dumps({'partitiontable': {**layout, **change}}).encode(), b'')
                    with self.subTest(change=change), patch.object(storage, 'run', return_value=response):
                        with self.assertRaises(ValueError):
                            storage.table()
                response = subprocess.CompletedProcess([], 0, json.dumps({'partitiontable': layout}).encode(), b'backup GPT used')
                with patch.object(storage, 'run', return_value=response), self.assertRaisesRegex(ValueError, 'clean GPT'):
                    storage.table()


class Operation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        policy = types.ModuleType('image_policy')
        for name in ('verify_privileges', 'verify_session_access', 'ready', 'validate_root',
                     'validate_handoff', 'diagnostics', 'stop_diagnostics'):
            setattr(policy, name, Mock(side_effect=AssertionError('Image policy must not run in storage tests')))
        policy.DIAGNOSTICS_EXECUTABLE = '/unused'
        spec = importlib.util.spec_from_file_location('orchard_device_storage_test', RUNTIME / 'device.py')
        self.device = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'image_policy': policy, 'storage': storage}), \
             patch.object(sys, 'path', [str(RUNTIME), str(RUNTIME.parents[1] / 'apple'), *sys.path]):
            spec.loader.exec_module(self.device)
        self.device.RUN = self.root
        (self.root / 'prepare.started').touch()
        (self.root / 'state.json').write_text(json.dumps({'phase': 'prepare', 'status': 'complete'}))
        self.boot = self.root / 'boot-id'
        self.boot.write_text('boot')
        self.boot_patch = patch.object(self.device.ssh_access, 'BOOT_ID', self.boot)
        self.boot_patch.start()
        self.device.no_faults = Mock()
        self.device.config = Mock(return_value={'root': {'expanded_bytes': 0}})
        self.partition = {'id': 'target', 'filesystem': 'unformatted', 'path': '/unused', 'bytes': 2 * 1024**3}
        self.request = {'selection_token': 'selection', 'target': 'target', 'boot_id': 'boot',
                        'operation_id': 'a' * 32, 'confirmation': 'format target'}
        (self.root / 'targets.json').write_text(json.dumps({'token': 'selection', 'partitions': [self.partition]}))
        self.inventory_patch = patch.object(storage, 'inventory', return_value=[self.partition])
        self.inventory_mock = self.inventory_patch.start()

    def tearDown(self):
        self.inventory_patch.stop()
        self.boot_patch.stop()
        self.temp.cleanup()

    def test_serial_bootstrap_refuses_wrong_identity_before_authorization(self):
        expected = {'profile':'j71b', 'ecid':1, 'boot_id':'boot'}
        with patch.object(self.device.identity, 'cached', return_value=expected), \
             patch.object(self.device, 'command') as command:
            for changes in ({'ecid':2}, {'boot_id':'old'}, {'profile':'other'}):
                with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'bootstrap identity differs'):
                    self.device.bootstrap({**expected, **changes, 'public_key':'ssh-ed25519 ' + 'A'*68})
            command.assert_not_called()

    def test_success_consumes_permission_and_invalidates_old_selection(self):
        def formatter(partition, fsuuid):
            self.assertTrue((self.root / 'format.started').is_file())
            self.assertEqual(json.loads((self.root / 'format.json').read_text())['status'], 'unknown')
            self.assertEqual(partition, self.partition)
            return {'filesystem': 'ext4', 'filesystem_uuid': fsuuid}
        with patch.object(storage, 'format_ext4', side_effect=formatter) as formatted:
            result = self.device.format_target(self.request)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['boot_id'], 'boot')
            self.assertFalse((self.root / 'targets.json').exists())
            with self.assertRaisesRegex(ValueError, 'already attempted'):
                self.device.format_target(self.request)
            self.assertEqual(formatted.call_count, 1)

    def test_rejected_consent_identity_boot_phase_and_existing_filesystem(self):
        changes = ({'confirmation': ''}, {'target': 'other'}, {'boot_id': 'old'},
                   {'selection_token': 'expired'}, {'operation_id': 'invalid'})
        with patch.object(storage, 'format_ext4') as formatted:
            for change in changes:
                with self.subTest(change=change), self.assertRaises(ValueError):
                    self.device.format_target({**self.request, **change})
                self.assertFalse((self.root / 'format.started').exists())
            self.partition['bytes'] = 1
            (self.root / 'targets.json').write_text(json.dumps({'token': 'selection', 'partitions': [self.partition]}))
            with self.assertRaisesRegex(ValueError, 'too small'):
                self.device.format_target(self.request)
            self.assertFalse((self.root / 'format.started').exists())
            self.partition['bytes'] = 2 * 1024**3
            self.partition['filesystem'] = 'ext4'
            (self.root / 'targets.json').write_text(json.dumps({'token': 'selection', 'partitions': [self.partition]}))
            with self.assertRaisesRegex(ValueError, 'without signatures'):
                self.device.format_target(self.request)
            (self.root / 'state.json').write_text('{"phase":"install","status":"complete"}')
            with self.assertRaisesRegex(ValueError, 'completed preparation'):
                self.device.format_target(self.request)
            formatted.assert_not_called()

    def test_failure_is_unknown_and_blocks_inventory_and_replay(self):
        with patch.object(storage, 'format_ext4', side_effect=TimeoutError('uncertain outcome')) as formatted:
            with self.assertRaises(TimeoutError):
                self.device.format_target(self.request)
            self.assertEqual(json.loads((self.root / 'format.json').read_text())['status'], 'unknown')
            self.inventory_mock.reset_mock()
            with self.assertRaisesRegex(ValueError, 'unverified'):
                self.device.inventory()
            self.inventory_mock.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'already attempted'):
                self.device.format_target(self.request)
            self.assertEqual(formatted.call_count, 1)


if __name__ == '__main__':
    unittest.main()

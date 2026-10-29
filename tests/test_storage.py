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
                                    (0, b'TYPE=ext4\nTYPE=ext3\n', b''),
                                    (0, b'TYPE=ext4\nUUID=value\nPTTYPE=dos\n', b''),
                                    (0, b'TYPE=ext4\nUSAGE=raid\n', b''), (0, b'', b'')):
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


OLD_UUID = '11111111-1111-4111-8111-111111111111'
NEW_UUID = '22222222-2222-4222-8222-222222222222'


class ResetOperation(unittest.TestCase):
    def setUp(self):
        Operation.setUp(self)
        self.partition.update(filesystem='ext4', filesystem_uuid=OLD_UUID)
        self.request.update(confirmation='reset target', expected_filesystem_uuid=OLD_UUID)
        self.snapshot()

    tearDown = Operation.tearDown

    def snapshot(self):
        (self.root / 'targets.json').write_text(json.dumps({'token': 'selection', 'partitions': [self.partition]}))

    def test_reset_receipt_empty_inspection_and_shared_one_shot_guard(self):
        def formatter(partition, fsuuid, *, reset):
            self.assertTrue(reset)
            self.assertTrue((self.root / 'format.started').exists())
            self.assertEqual(json.loads((self.root / 'format.json').read_text())['status'], 'unknown')
            self.assertNotEqual(fsuuid, OLD_UUID)
            return {**partition, 'filesystem_uuid': fsuuid}
        with patch.object(storage, 'format_ext4', side_effect=formatter) as formatted, \
             patch.object(storage, 'verify_empty') as empty:
            result = self.device.reset_target(self.request)
            self.assertEqual(result['operation'], 'reset-target')
            self.assertEqual(result['previous_filesystem_uuid'], OLD_UUID)
            self.assertEqual(result['status'], 'complete')
            empty.assert_called_once()
            self.assertFalse((self.root / 'targets.json').exists())
            for operation in (self.device.reset_target, self.device.format_target):
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    operation(self.request)
            self.assertEqual(formatted.call_count, 1)

    def test_abort_after_completed_reset_reports_destroyed_contents(self):
        self.device.ROOT = self.root / 'mount'
        with patch.object(storage, 'format_ext4', return_value={**self.partition, 'filesystem_uuid': NEW_UUID}), \
             patch.object(storage, 'verify_empty'):
            self.device.reset_target(self.request)
        for replacement in (False, True):
            installation = self.root / 'installation.json'
            if replacement:
                installation.write_text(json.dumps({'target': self.partition, 'replacement_started': True}))
            with patch.object(self.device, 'cleanup_leftovers', return_value=0), \
                 patch.object(self.device, 'partition_record', return_value=self.partition), \
                 patch.object(self.device, 'command'), patch.object(Path, 'read_text', autospec=True) as read:
                original_read = self._read_for_abort
                read.side_effect = original_read
                result = self.device.abort({})
            self.assertIn('reset completed', result['message'])
            self.assertIn('previous contents on partition target were destroyed', result['message'])
            self.assertNotIn('Existing directories were not deleted', result['message'])
            self.assertEqual('Replacement deletion was started' in result['message'], replacement)
            self.assertTrue(result['reboot_required'])
            self.assertEqual(json.loads((self.root / 'format.json').read_text())['status'], 'complete')

    def _read_for_abort(self, path, *args, **kwargs):
        if str(path) == '/sys/class/block/unused/dev':
            return '0:0'
        if str(path) == '/proc/self/mountinfo':
            return ''
        with path.open('r', *args, **kwargs) as stream:
            return stream.read()

    def test_abort_unknown_reset_avoids_cleanup_and_retains_uncertainty(self):
        self.device.ROOT = self.root / 'mount'
        with patch.object(storage, 'format_ext4', side_effect=TimeoutError('lost result')):
            with self.assertRaises(TimeoutError):
                self.device.reset_target(self.request)
        for missing_receipt in (False, True):
            if missing_receipt:
                (self.root / 'format.json').unlink()
            with patch.object(self.device, 'cleanup_leftovers') as cleanup, \
                 patch.object(self.device, 'mount_target') as mount, \
                 patch.object(self.device, 'unmount_target') as unmount:
                result = self.device.abort({})
                cleanup.assert_not_called()
                mount.assert_not_called()
                unmount.assert_not_called()
            self.assertIn('reset outcome is unknown', result['message'])
            self.assertIn('Do not retry reset', result['message'])
            self.assertNotIn('Existing directories were not deleted', result['message'])
            self.assertTrue((self.root / 'format.started').exists())
            self.assertTrue(result['reboot_required'])

    def test_abort_without_reset_retains_existing_outcome(self):
        self.device.ROOT = self.root / 'mount'
        with patch.object(self.device, 'cleanup_leftovers', return_value=0):
            result = self.device.abort({})
        self.assertIn('Existing directories were not deleted', result['message'])
        self.assertNotIn('Filesystem reset', result['message'])

    def test_invalid_consent_uuid_signature_phase_and_changed_target_never_write(self):
        with patch.object(storage, 'format_ext4') as formatted:
            for change in ({'confirmation': ''}, {'confirmation': 'format target'},
                           {'expected_filesystem_uuid': NEW_UUID}, {'boot_id': 'old'},
                           {'selection_token': 'old'}, {'operation_id': 'bad'}, {'target': 'other'}):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    self.device.reset_target({**self.request, **change})
            for kind in ('unformatted', 'ext2', 'ext3', 'crypto_LUKS', 'linux_raid_member', 'partition-table'):
                self.partition['filesystem'] = kind
                self.snapshot()
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    self.device.reset_target(self.request)
            self.partition['filesystem'] = 'ext4'
            self.snapshot()
            with patch.object(storage, 'inventory', return_value=[{**self.partition, 'bytes': 1}]):
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.device.reset_target(self.request)
            (self.root / 'install.started').touch()
            with self.assertRaisesRegex(ValueError, 'completed preparation'):
                self.device.reset_target(self.request)
            self.assertFalse((self.root / 'format.started').exists())
            formatted.assert_not_called()

    def test_invalid_filesystem_uuid_and_capacity_are_refused_before_attempt(self):
        with patch.object(storage, 'format_ext4') as formatted:
            for value in ('bad', '00000000-0000-0000-0000-000000000000', None):
                self.partition['filesystem_uuid'] = value
                self.snapshot()
                with self.subTest(value=value), self.assertRaises((ValueError, TypeError, AttributeError)):
                    self.device.reset_target({**self.request, 'expected_filesystem_uuid': value})
            self.partition.update(filesystem_uuid=OLD_UUID, bytes=1)
            self.snapshot()
            with self.assertRaisesRegex(ValueError, 'too small'):
                self.device.reset_target(self.request)
            self.assertFalse((self.root / 'format.started').exists())
            formatted.assert_not_called()

    def test_format_and_verification_failures_remain_unknown_and_block_replay(self):
        for phase in ('mkfs', 'empty'):
            with self.subTest(phase=phase):
                with patch.object(storage, 'format_ext4', return_value=self.partition,
                                  side_effect=TimeoutError('lost result') if phase == 'mkfs' else None), \
                     patch.object(storage, 'verify_empty', side_effect=ValueError('not empty')):
                    with self.assertRaises((TimeoutError, ValueError)):
                        self.device.reset_target(self.request)
                self.assertEqual(json.loads((self.root / 'format.json').read_text())['status'], 'unknown')
                with self.assertRaisesRegex(ValueError, 'unverified'):
                    self.device.inventory()
                with self.assertRaisesRegex(ValueError, 'already attempted'):
                    self.device.reset_target(self.request)
                (self.root / 'format.started').unlink()

    def test_initialization_consumes_reset_permission_too(self):
        self.partition['filesystem'] = 'unformatted'
        self.snapshot()
        with patch.object(storage, 'format_ext4', return_value={'filesystem': 'ext4'}):
            self.device.format_target({**self.request, 'confirmation': 'format target'})
        with self.assertRaisesRegex(ValueError, 'already attempted'):
            self.device.reset_target(self.request)

    def test_capability_is_absent_in_launcher_and_launcher_refuses_reset(self):
        import base64
        import io
        from contextlib import redirect_stdout
        for mode in ('installer', 'launcher'):
            with patch.object(self.device, 'config', return_value={'release': 'test', 'mode': mode}), \
                 patch.object(self.device, 'system_ready', return_value=False), \
                 patch.object(self.device.identity, 'cached', return_value=None), \
                 patch.object(self.device.ssh_access, 'revoked', return_value=False):
                self.assertEqual('reset-ext4-v1' in self.device.status({})['capabilities'], mode == 'installer')
                if mode == 'launcher':
                    payload = base64.urlsafe_b64encode(json.dumps(self.request).encode()).decode()
                    with patch.object(sys, 'argv', ['orchard-device', 'reset-target', payload]), \
                         redirect_stdout(io.StringIO()), self.assertRaises(SystemExit), \
                         patch.object(storage, 'format_ext4') as formatted:
                        self.device.main()
                    formatted.assert_not_called()


class ResetStorage(unittest.TestCase):
    def test_single_force_only_for_reset_and_refencing_after_command_failure(self):
        import stat
        partition = {'filesystem': 'ext4', 'filesystem_uuid': OLD_UUID,
                     'path': '/dev/owned', 'device_number': 42, 'id': 'target',
                     'bytes': 16 * 1024**3, 'start': 1048576, 'gpt_sha256': 'gpt'}
        formatted = {**partition, 'filesystem_uuid': NEW_UUID}
        for reset, fail in ((True, False), (True, True), (False, False)):
            original = partition if reset else {**partition, 'filesystem': 'unformatted'}
            calls = []
            def command(*args, **kwargs):
                calls.append(args)
                if args[0] == str(storage.TOOLS / 'mke2fs') and fail:
                    raise TimeoutError('mkfs timeout')
            with patch.object(storage, 'fenced'), \
                 patch.object(storage, 'record', side_effect=[original, original, formatted]), \
                 patch.object(storage.os, 'open', return_value=10), \
                 patch.object(storage.os, 'fstat', return_value=types.SimpleNamespace(st_mode=stat.S_IFBLK, st_rdev=42)), \
                 patch.object(storage.os, 'fsync'), patch.object(storage.os, 'close'), \
                 patch.object(storage, 'checked', side_effect=command):
                if fail:
                    with self.assertRaises(TimeoutError):
                        storage.format_ext4(original, NEW_UUID, reset=reset)
                else:
                    storage.format_ext4(original, NEW_UUID, reset=reset)
            mkfs = next(args for args in calls if args[0] == str(storage.TOOLS / 'mke2fs'))
            self.assertEqual(mkfs.count('-F'), int(reset))
            self.assertIn('nodiscard,lazy_itable_init=0,lazy_journal_init=0', mkfs)
            self.assertEqual(calls[-1], ('blockdev', '--setro', '/proc/self/fd/10'))

    def test_empty_inspection_rejects_contents_and_symlinks_and_always_unmounts(self):
        for contents in ('empty', 'lost+found', 'old-root', 'symlink', 'nonempty-lost+found'):
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / 'mount'
                root.mkdir()
                if contents in ('lost+found', 'nonempty-lost+found'):
                    (root / 'lost+found').mkdir()
                    if contents == 'nonempty-lost+found':
                        (root / 'lost+found/file').touch()
                elif contents == 'old-root':
                    (root / 'omarchy').mkdir()
                elif contents == 'symlink':
                    (root / 'lost+found').symlink_to('/unused')
                partition = {'path': '/dev/owned', 'filesystem': 'ext4'}
                with patch.object(storage, 'record', return_value=partition), \
                     patch.object(storage, 'fenced'), patch.object(storage, 'checked') as command:
                    if contents in ('empty', 'lost+found'):
                        storage.verify_empty(partition, root)
                    else:
                        with self.assertRaisesRegex(ValueError, 'not empty'):
                            storage.verify_empty(partition, root)
                    self.assertEqual(command.call_args.args, ('umount', str(root)))

    def test_existing_reset_checks_identity_before_opening_write_window(self):
        partition = {'path': '/dev/owned', 'filesystem': 'ext4', 'filesystem_uuid': OLD_UUID}
        with patch.object(storage, 'fenced'), patch.object(storage.os, 'open') as opened:
            for observed, fsuuid in (({**partition, 'bytes': 1}, NEW_UUID), (partition, OLD_UUID)):
                with patch.object(storage, 'record', return_value=observed), self.assertRaises(ValueError):
                    storage.format_ext4(partition, fsuuid, reset=True)
            opened.assert_not_called()

    def test_fence_rejects_mount_swap_holders_and_writable_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            block = root / storage.DISK.name / 'partition'
            block.mkdir(parents=True)
            (block / 'dev').write_text('0:0')
            (block.parent / 'dev').write_text('0:1')
            (block / 'ro').write_text('1')
            (block / 'holders').mkdir()
            (block.parent / 'holders').mkdir()
            mounts, swaps = root / 'mounts', root / 'swaps'
            mounts.write_text('')
            swaps.write_text('Filename Type Size Used Priority\n')
            with patch.object(storage, 'SYS', root), patch.object(storage, 'MOUNTS', mounts), \
                 patch.object(storage, 'SWAPS', swaps):
                storage.fenced(block)
                for condition in ('mounted', 'swap', 'holder', 'writable'):
                    with self.subTest(condition=condition):
                        if condition == 'mounted':
                            mounts.write_text('1 0 0:0 / /target rw\n')
                        elif condition == 'swap':
                            swaps.write_text(str(block / 'dev') + ' partition 1 0 0\n')
                            # Include the normal header: the parser skips it.
                            swaps.write_text('Filename Type Size Used Priority\n' + swaps.read_text())
                        elif condition == 'holder':
                            (block / 'holders/active').touch()
                        else:
                            (block / 'ro').write_text('0')
                        with patch.object(storage.stat, 'S_ISBLK', return_value=True), self.assertRaises(ValueError):
                            storage.fenced(block)
                        mounts.write_text('')
                        swaps.write_text('Filename Type Size Used Priority\n')
                        (block / 'holders/active').unlink(missing_ok=True)
                        (block / 'ro').write_text('1')


if __name__ == '__main__':
    unittest.main()

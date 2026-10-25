"""Dispatcher lock ownership with real flock; no hardware or storage commands."""
import base64
from contextlib import redirect_stdout
import fcntl
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime/j71b'


class OperationLock(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        policy = types.ModuleType('image_policy')
        for name in ('verify_privileges', 'verify_session_access', 'ready', 'validate_root',
                     'validate_handoff', 'diagnostics', 'stop_diagnostics'):
            setattr(policy, name, Mock(side_effect=AssertionError('Image policy must not run')))
        policy.DIAGNOSTICS_EXECUTABLE = '/unused'
        spec = importlib.util.spec_from_file_location('device_operation_lock_test', RUNTIME / 'device.py')
        self.device = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'image_policy': policy}), \
             patch.object(sys, 'path', [str(RUNTIME), str(RUNTIME.parents[1] / 'apple'), *sys.path]):
            spec.loader.exec_module(self.device)
        self.device.RUN = self.root
        self.device.CONFIG = self.root / 'release.json'
        self.device.CONFIG.write_text('{"mode":"recovery"}')
        (self.root / 'state.json').write_text('{"phase":"install","status":"running"}')
        (self.root / 'install.started').write_text('request A')
        self.lock_path = self.root / 'operation.lock'
        self.lock_path.write_text('lock contents must not be truncated')
        self.device.abort = Mock(return_value={'message': 'Fresh recovery required'})

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.root.iterdir() if path.is_file()}

    def invoke(self, operation, request=None, *, failure=False):
        payload = base64.urlsafe_b64encode(json.dumps(request or {'protocol': 1}).encode()).decode()
        output = io.StringIO()
        with patch.object(sys, 'argv', ['orchard-device', operation, payload]), redirect_stdout(output):
            if failure:
                with self.assertRaises(SystemExit) as exit_status:
                    self.device.main()
                self.assertEqual(exit_status.exception.code, 1)
            else:
                self.device.main()
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def assert_locked(self):
        # Independent open-file description: it must not acquire the active lock.
        with self.lock_path.open('a') as contender:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def assert_unlocked(self):
        with self.lock_path.open('a') as contender:
            fcntl.flock(contender, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_busy_request_cannot_poison_active_session_or_attempt_cleanup(self):
        for operation in ('prepare', 'install', 'handoff', 'launch'):
            with self.subTest(operation=operation), self.lock_path.open('a') as owner:
                fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
                handler = Mock(side_effect=AssertionError('Busy handler must not execute'))
                before = self.snapshot()
                with patch.dict(self.device.OPERATIONS, {operation: handler}):
                    events = self.invoke(operation, failure=True)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(events[-1]['message'], 'Operation already running')
                self.assertFalse(events[-1]['retry'])
                handler.assert_not_called()
                self.device.abort.assert_not_called()

    def test_busy_request_preserves_existing_failure_marker(self):
        marker = self.root / 'failure.json'
        marker.write_text('{"operation":"request A","error":"existing evidence"}')
        before = self.snapshot()
        with self.lock_path.open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.invoke('install', failure=True)
        self.assertEqual(self.snapshot(), before)
        self.device.abort.assert_not_called()

    def test_validation_errors_do_not_mark_failure_or_clean_up(self):
        for operation, request, mode in (
            ('prepare', {'protocol': 2}, 'recovery'),
            ('install', {'protocol': 1}, 'launcher'),
            ('unknown', {'protocol': 1}, 'recovery'),
        ):
            with self.subTest(operation=operation, request=request, mode=mode):
                self.device.CONFIG.write_text(json.dumps({'mode': mode}))
                before = self.snapshot()
                self.invoke(operation, request, failure=True)
                self.assertEqual(self.snapshot(), before)
                self.device.abort.assert_not_called()
        self.assert_unlocked()

    def test_failure_recording_and_cleanup_hold_the_original_lock(self):
        for operation in ('prepare', 'install', 'handoff', 'launch'):
            with self.subTest(operation=operation):
                real_atomic = self.device.atomic
                def record(path, data):
                    self.assert_locked()
                    real_atomic(path, data)
                def cleanup(request):
                    self.assert_locked()
                    self.assertEqual(json.loads((self.root / 'failure.json').read_text())['operation'], operation)
                    return {'message': 'Fresh recovery required'}
                handler = Mock(side_effect=RuntimeError('execution failed'))
                real_flock = fcntl.flock
                with patch.dict(self.device.OPERATIONS, {operation: handler}), \
                     patch.object(self.device, 'atomic', side_effect=record), \
                     patch.object(self.device, 'abort', side_effect=cleanup) as aborted, \
                     patch.object(self.device.fcntl, 'flock', wraps=real_flock) as flock:
                    events = self.invoke(operation, failure=True)
                    # The dispatch lock is acquired only once. The other two
                    # flock calls are our independent recording/cleanup probes.
                    self.assertEqual(flock.call_count, 3)
                    aborted.assert_called_once_with({})
                self.assertIn('execution failed', events[-1]['message'])
                self.assertIn('Fresh recovery required', events[-1]['message'])
                self.assert_unlocked()

    def test_cleanup_failure_is_reported_and_lock_is_released(self):
        def cleanup(request):
            self.assert_locked()
            raise RuntimeError('cleanup failed')
        with patch.dict(self.device.OPERATIONS, {'install': Mock(side_effect=RuntimeError('execution failed'))}), \
             patch.object(self.device, 'abort', side_effect=cleanup):
            events = self.invoke('install', failure=True)
        self.assertIn('Cleanup incomplete: cleanup failed', events[-1]['message'])
        self.assertEqual(json.loads((self.root / 'failure.json').read_text())['error'], 'execution failed')
        self.assert_unlocked()

    def test_success_releases_lock_without_failure_handling(self):
        def execute(request):
            self.assert_locked()
            return {'complete': True}
        with patch.dict(self.device.OPERATIONS, {'install': execute}):
            events = self.invoke('install')
        self.assertEqual(events[-1]['result'], {'complete': True})
        self.assertFalse((self.root / 'failure.json').exists())
        self.device.abort.assert_not_called()
        self.assert_unlocked()

    def test_read_only_status_and_diagnostics_remain_available_while_busy(self):
        with self.lock_path.open('a') as owner:
            fcntl.flock(owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for operation in ('status', 'diagnostics'):
                with self.subTest(operation=operation), \
                     patch.dict(self.device.OPERATIONS, {operation: lambda request: {'read_only': True}}):
                    before = self.snapshot()
                    events = self.invoke(operation)
                    self.assertEqual(events[-1]['result'], {'read_only': True})
                    self.assertEqual(self.snapshot(), before)


if __name__ == '__main__':
    unittest.main()

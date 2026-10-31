"""orchard pull/push retry once when the SSH connection drops."""
import io, tempfile, subprocess, unittest
from pathlib import Path
from unittest import mock
from orchard import ram_session

class Retry(unittest.TestCase):
    def test_a_dropped_pull_is_retried_once_from_the_start(self):
        calls = []
        def run(argv, stdin=None, stdout=None):
            calls.append(argv)
            stdout.write(b'partial' if len(calls) == 1 else b'complete')
            return subprocess.CompletedProcess(argv, 255 if len(calls) == 1 else 0)
        with tempfile.TemporaryFile() as stream, mock.patch.object(ram_session.subprocess, 'run', side_effect=run), \
                mock.patch.object(ram_session, 'connect', return_value=['ssh', 'again']):
            argv = ram_session._transfer(['ssh', 'first'], 'cat x', stdout=stream)
            stream.seek(0)
            self.assertEqual(stream.read(), b'complete')
        self.assertEqual(argv, ['ssh', 'again'])
        self.assertEqual(len(calls), 2)

    def test_a_device_error_is_not_retried(self):
        with mock.patch.object(ram_session.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1)) as run:
            with self.assertRaisesRegex(ValueError, 'exit 1'):
                ram_session._transfer(['ssh'], 'cat x', stdin=io.BytesIO(b'data'))
        self.assertEqual(run.call_count, 1)

if __name__ == "__main__":
    unittest.main()

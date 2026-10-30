"""Multipart release acquisition and quiet, aligned terminal output."""
import contextlib
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from orchard_cli import downloads, releases
from orchard_cli.progress import Clock, Progress


class Terminal(io.StringIO):
    def isatty(self):
        return True


class DownloadUI(unittest.TestCase):
    def test_multipart_has_no_unsolicited_stdout(self):
        data = bytes(range(80))
        class Response(io.BytesIO):
            status = 206
        def response(request, timeout):
            start, end = map(int, request.get_header('Range')[6:].split('-'))
            result = Response(data[start:end + 1])
            result.headers = {'Content-Range': f'bytes {start}-{end}/{len(data)}'}
            return result
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as output:
            reports = []
            with patch.object(downloads, 'open_url', side_effect=response):
                downloads.download('https://example.invalid/a', len(data), Path(directory) / 'a',
                                   chunk_size=8, progress=reports.append)
            self.assertEqual(output.getvalue(), '')
            self.assertEqual(reports[-1]['assembled_bytes'], len(data))

    def test_named_download_rows_align_and_update_in_place(self):
        terminal = Terminal()
        progress = Progress(terminal)
        for name in ('Omarchy A', 'Recovery', 'iPadOS firmware'):
            task = progress.task(name)
            task.transfer(0, 100000000)
            task.transfer(100000000, 100000000)
        text = terminal.getvalue()
        self.assertIn('\033[', text)
        rows = [line.split('\033[2K')[-1] for line in text.splitlines() if '\033[2K' in line]
        self.assertEqual(len({row.index('%') for row in rows}), 1)
        self.assertNotIn('Assembled', text)
        self.assertNotIn('0.0/0.0', text)

    def test_internal_device_events_are_private_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory, patch('orchard_cli.cache.cache_root', return_value=Path(directory)):
            output = Terminal()
            progress = Progress(output)
            progress.event({'event': 'progress', 'message': 'TOUCH_INPUT_READY'})
            self.assertEqual(output.getvalue(), '')
            self.assertIn('TOUCH_INPUT_READY', Path(progress.log_path).read_text())
            self.assertEqual(Path(progress.log_path).stat().st_mode & 0o777, 0o600)

    def test_release_assets_start_concurrently(self):
        barrier = threading.Barrier(3)
        document = {'protocol': 1, 'version': '1', 'name': 'Test Linux',
                    'pongo': {}, 'boot': {}, 'root': {}}
        def materialize(*args, **kwargs):
            barrier.wait(timeout=2)
            return Path('verified')
        with patch.object(releases, 'select_manifest', return_value=(document, None)), \
             patch.object(releases, 'materialize', side_effect=materialize):
            _, paths = releases.resolve('test', 'j71b')
        self.assertEqual(set(paths), {'pongo', 'recovery', 'root'})

    def test_resume_is_verified_before_cache_promotion(self):
        data = b'full verified content'
        receipt = {'url': 'https://example.invalid/asset', 'bytes': len(data),
                   'sha256': hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as directory, patch.object(releases, 'cache_root', return_value=Path(directory).resolve()):
            folder = Path(directory) / 'artifacts/sha256'
            folder.mkdir(parents=True)
            partial = folder / (receipt['sha256'] + '.download')
            partial.write_bytes(data[:4])
            def finish(url, total, destination, **kwargs):
                self.assertEqual(destination.read_bytes(), data[:4])
                destination.write_bytes(data)
            with patch.object(releases, 'download', side_effect=finish):
                target = releases.materialize(receipt, None)
            self.assertEqual(target.read_bytes(), data)
            self.assertFalse(partial.exists())

    def test_overlapping_download_exclusions_count_wall_time_once(self):
        with patch('orchard_cli.progress.time.monotonic', side_effect=[0, 1, 2, 5, 6]):
            clock = Clock()
            with clock.exclude('download'):
                with clock.exclude('download'):
                    pass
            result = clock.result()
        self.assertEqual(result['excluded_seconds']['download'], 4)
        self.assertEqual(result['install_seconds'], 2)


if __name__ == '__main__':
    unittest.main()

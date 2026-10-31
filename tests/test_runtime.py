"""Manifest resolution, downloads and installed-wheel independence; no devices."""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.request

from orchard.integrity import digest
from orchard.http import HTTPSRedirect
from orchard import releases
from orchard.command import main
from orchard.manifests import ManifestError
from orchard.progress import Progress

CLI = Path(__file__).resolve().parents[1]
FIXTURE = CLI / 'tests/fixtures/first'


class Runtime(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.cache = patch('orchard.releases.cache_root', return_value=self.root / 'store')
        self.cache.start()

    def tearDown(self):
        self.cache.stop()
        self.temporary.cleanup()

    def test_adapter_boot_sequence_timeouts_and_no_retry_are_preserved(self):
        from orchard import devices
        from orchard.apple import device as adapter_module
        adapter = devices.load('j71b')
        from orchard.apple import boot
        device = {'mode': 'dfu', 'location': 'selected-port', 'ecid': 123}
        assets = {'pongo': self.root / 'pongo', 'recovery': self.root / 'boot'}
        record = {'assets': {'recovery': {'sha256': 'expected-digest'}}}
        with patch('orchard.state.cache_root', return_value=self.root), \
             patch.object(boot.shutil, 'which', return_value='/tools/palera1n'), \
             patch.object(adapter_module.usb, 'selected') as selected, \
             patch.object(boot, 'wait_mode') as wait, \
             patch.object(boot, 'run_process_group') as bootstrap, \
             patch.object(boot.pongo, 'upload') as upload:
            adapter.boot(device, assets, record, Mock())
            self.assertEqual(bootstrap.call_count, 1)
            self.assertEqual(bootstrap.call_args.args[0], ['/tools/palera1n', '-p', '-k', str(assets['pongo'])])
            self.assertEqual(bootstrap.call_args.kwargs['timeout'], 120)
            self.assertEqual([call.args[1] for call in wait.call_args_list], ['pongo', 'linux'])
            self.assertEqual(wait.call_args.kwargs, {'seconds': 120, 'chip': adapter.CHIP, 'board': adapter.BOARD, 'product': adapter.PRODUCT})
            self.assertEqual(upload.call_args.args[:3], (assets['recovery'], 'selected-port', 'expected-digest'))
            bootstrap.side_effect = subprocess.TimeoutExpired('palera1n', 120)
            upload.reset_mock()
            with self.assertRaisesRegex(RuntimeError, 'outcome unknown'):
                adapter.boot(device, assets, record, Mock())
            self.assertEqual(bootstrap.call_count, 2) # one success, one failed call; no retry.
            upload.assert_not_called()

    def test_bootstrap_timeout_stops_spawned_children(self):
        # palera1n posix_spawns checkra1n; a surviving checkra1n would exploit
        # the next DFU device behind a later command's back.
        from orchard.apple import boot
        pidfile = self.root / 'child.pid'
        script = f'sleep 300 & echo $! > {pidfile}; wait'
        with self.assertRaises(subprocess.TimeoutExpired):
            boot.run_process_group(['sh', '-c', script], timeout=1)
        child = int(pidfile.read_text())
        for _ in range(50):
            try:
                state = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):  # gone mid-read
                break
            if state == 'Z':
                break
            time.sleep(0.05)
        else:
            os.kill(child, 9)
            self.fail('bootstrap child outlived the timeout')

    def test_bootstrap_sigterm_stops_spawned_children(self):
        pidfile = self.root / 'child.pid'
        script = (
            'import sys\n'
            'from orchard.apple import boot\n'
            'boot.run_process_group(["sh", "-c", "sleep 300 & echo $! > " + sys.argv[1] + "; wait"], timeout=60)\n')
        env = {**os.environ, 'PYTHONPATH': str(CLI / 'src') + os.pathsep + os.environ.get('PYTHONPATH', '')}
        orchard = subprocess.Popen([sys.executable, '-c', script, str(pidfile)], env=env)
        try:
            for _ in range(100):
                if pidfile.exists() and pidfile.read_text().strip():
                    break
                time.sleep(0.05)
            child = int(pidfile.read_text())
            orchard.terminate()
            self.assertEqual(orchard.wait(timeout=10), 128 + 15)
        finally:
            orchard.kill()
        for _ in range(50):
            try:
                state = Path(f'/proc/{child}/stat').read_text().rsplit(')', 1)[1].split()[0]
            except (FileNotFoundError, ProcessLookupError):  # gone mid-read
                break
            if state == 'Z':
                break
            time.sleep(0.05)
        else:
            os.kill(child, 9)
            self.fail('bootstrap child outlived SIGTERM to Orchard')

    def test_bootstrap_failure_is_reported(self):
        from orchard.apple import boot
        with self.assertRaises(subprocess.CalledProcessError):
            boot.run_process_group(['sh', '-c', 'exit 3'], timeout=5)
        boot.run_process_group(['true'], timeout=5)

    def test_manifest_becomes_existing_workflow_inputs(self):
        record, assets = releases.resolve(FIXTURE / 'index.toml', 'j71b')
        self.assertEqual(record['release'], 'example-1')
        self.assertEqual(record['default_root'], '/first')
        self.assertEqual(set(assets), {'pongo', 'recovery', 'root', 'layer'})
        self.assertEqual(record['platform'], 'apple-16k')
        self.assertEqual(record['assets']['root']['sha256'], digest(assets['root']))
        self.assertEqual(record['assets']['layer']['sha256'], digest(assets['layer']))

    def test_developer_boot_does_not_resolve_root_archive(self):
        record, assets = releases.resolve(CLI / 'tests/fixtures/developer/develop.toml', 'j71b', purpose='boot')
        self.assertEqual(set(assets), {'pongo', 'recovery'})
        self.assertIsNone(record['release'])
        with self.assertRaises(ValueError):
            releases.resolve(CLI / 'tests/fixtures/developer/develop.toml', 'j71b')

    def test_catalogue_registration_and_alias(self):
        releases.register('fixture-linux', FIXTURE / 'index.toml')
        self.assertEqual(releases.resolve('fixture-linux', 'j71b')[0]['profile'], 'j71b')
        self.assertEqual((self.root / 'store/distros.toml').stat().st_mode & 0o777, 0o600)
        releases.register('wrong-name', FIXTURE / 'index.toml')
        with self.assertRaisesRegex(ValueError, 'identity differs'):
            releases.resolve('wrong-name', 'j71b')
        with self.assertRaisesRegex(ValueError, 'Unknown distro'):
            releases.resolve('not-published', 'j71b')

    def test_remote_index_manifest_and_assets_are_pinned_and_cached(self):
        manifest = (FIXTURE / 'release.toml').read_text()
        manifest = manifest.replace('file = "fixtures/root.tar.gz"', 'url = "https://example.invalid/root"')
        manifest = manifest.replace('file = "fixtures/boot-stub.txt"', 'url = "https://example.invalid/boot"')
        manifest = manifest.replace('file = "fixtures/platform.tar.gz"', 'url = "https://example.invalid/layer"')
        manifest_bytes = manifest.encode()
        index = ('schema=2\nkind="orchard.index"\ndistro="fixture-linux"\n'
                 '[release]\nurl="https://example.invalid/manifest"\n'
                 f'bytes={len(manifest_bytes)}\nsha256="{hashlib.sha256(manifest_bytes).hexdigest()}"\n').encode()
        responses = {
            'https://example.invalid/index': index,
            'https://example.invalid/manifest': manifest_bytes,
            'https://example.invalid/root': (FIXTURE / 'fixtures/root.tar.gz').read_bytes(),
            'https://example.invalid/boot': (FIXTURE / 'fixtures/boot-stub.txt').read_bytes(),
            'https://example.invalid/layer': (FIXTURE / 'fixtures/platform.tar.gz').read_bytes(),
        }
        def download(url, total, destination, **kwargs):
            Path(destination).write_bytes(responses[url])
        with patch('orchard.releases.open_url', side_effect=lambda url: io.BytesIO(responses[url])) as request, \
             patch('orchard.releases.download', side_effect=download) as transfer:
            record, assets = releases.resolve('https://example.invalid/index', 'j71b')
            self.assertEqual(record['release'], 'example-1')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(transfer.call_count, 4) # manifest and three distinct blobs; shared Pongo downloads once.
            self.assertEqual(assets['root'].read_bytes(), responses['https://example.invalid/root'])
            self.assertEqual(assets['layer'].read_bytes(), responses['https://example.invalid/layer'])
            self.assertEqual(record['platform'], 'apple-16k')
            releases.resolve('https://example.invalid/index', 'j71b')
            self.assertEqual(request.call_count, 2) # refresh index; immutable files reuse receipts.
            self.assertEqual(transfer.call_count, 4)

    @unittest.skipUnless(shutil.which('openssl'), 'openssl required for isolated loopback TLS test')
    def test_real_https_stack_downloads_index_manifest_and_assets(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import ssl
        import threading
        key, cert = self.root / 'key.pem', self.root / 'cert.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(key), '-out', str(cert), '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1'],
                       check=True, capture_output=True)
        responses = {}
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                data = responses[self.path]
                total = len(data)
                if self.headers.get('Range'):
                    start, end = map(int, self.headers['Range'].removeprefix('bytes=').split('-'))
                    data = data[start:end + 1]
                    self.send_response(206)
                    self.send_header('Content-Range', f'bytes {start}-{end}/{total}')
                else:
                    self.send_response(200)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        base = 'https://127.0.0.1:' + str(server.server_port)
        manifest = (FIXTURE / 'release.toml').read_text()
        manifest = manifest.replace('file = "fixtures/root.tar.gz"', 'url = "' + base + '/root"')
        manifest = manifest.replace('file = "fixtures/boot-stub.txt"', 'url = "' + base + '/boot"')
        manifest = manifest.replace('file = "fixtures/platform.tar.gz"', 'url = "' + base + '/layer"')
        data = manifest.encode()
        responses['/manifest'] = data
        responses['/root'] = (FIXTURE / 'fixtures/root.tar.gz').read_bytes()
        responses['/boot'] = (FIXTURE / 'fixtures/boot-stub.txt').read_bytes()
        responses['/layer'] = (FIXTURE / 'fixtures/platform.tar.gz').read_bytes()
        responses['/index'] = ('schema=2\nkind="orchard.index"\ndistro="fixture-linux"\n'
            '[release]\nurl="' + base + '/manifest"\n'
            f'bytes={len(data)}\nsha256="{hashlib.sha256(data).hexdigest()}"\n').encode()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Trust only this test CA; certificate/hostname verification stays enabled.
            client_context = ssl.create_default_context(cafile=str(cert))
            self.assertEqual(client_context.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(client_context.check_hostname)
            with patch('ssl._create_default_https_context', return_value=client_context):
                record, assets = releases.resolve(base + '/index', 'j71b')
                self.assertEqual(record['profile'], 'j71b')
                self.assertEqual(assets['root'].read_bytes(), responses['/root'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_remote_manifest_cannot_read_host_local_paths(self):
        data = (FIXTURE / 'release.toml').read_bytes()
        with patch('orchard.releases.open_url', return_value=io.BytesIO(data)):
            with self.assertRaisesRegex(ValueError, 'Remote manifests must use exact URLs'):
                releases.resolve('https://example.invalid/manifest', 'j71b')

    def test_download_size_and_hash_failures_never_promote(self):
        data = b'expected'
        receipt = {'url': 'https://example.invalid/file', 'bytes': len(data),
                   'sha256': hashlib.sha256(data).hexdigest()}
        for body in (b'short', b'too long by far', b'bad-hash'):
            with self.subTest(body=body):
                def download(url, total, destination, **kwargs):
                    Path(destination).write_bytes(body)
                with patch('orchard.releases.download', side_effect=download):
                    with self.assertRaises(ValueError):
                        releases.materialize(receipt, None)
                self.assertFalse((self.root / 'store/artifacts/sha256' / receipt['sha256']).exists())

    def test_corrupt_cached_blob_fails_without_silent_download(self):
        receipt = {'url': 'https://example.invalid/file', 'bytes': 4,
                   'sha256': hashlib.sha256(b'data').hexdigest()}
        folder = self.root / 'store/artifacts/sha256'
        folder.mkdir(parents=True)
        (folder / receipt['sha256']).write_bytes(b'evil')
        with patch('orchard.releases.open_url') as request:
            with self.assertRaises(ValueError):
                releases.materialize(receipt, None)
            request.assert_not_called()

    def test_https_downgrade_redirect_rejected(self):
        request = urllib.request.Request('https://example.invalid/file')
        with self.assertRaises(ValueError):
            HTTPSRedirect().redirect_request(request, None, 302, 'redirect', {},
                                                     'http://example.invalid/file')

    def test_incompatible_minimum_protocol_device_and_layout_fail_before_assets(self):
        original = (FIXTURE / 'release.toml').read_text()
        for old, new in (('min_orchard = "0.1.0"', 'min_orchard = "999.0.0"'),
                         ('protocol = 1', 'protocol = 999'),
                         ('boards = ["j71b"]', 'boards = ["wrong-device"]'),
                         ('[platforms.apple-16k]', '[platforms.apple-4k]'),
                         ('default_root = "/first"', 'default_root = "/"')):
            with self.subTest(new=new):
                path = self.root / 'incompatible.toml'
                path.write_text(original.replace(old, new))
                with patch('orchard.releases.materialize') as assets:
                    with self.assertRaises(ValueError):
                        releases.resolve(path, 'j71b')
                    assets.assert_not_called()

    def test_low_level_boot_uses_preserved_adapter_and_never_installs(self):
        adapter = Mock(PROFILE='j71b')
        device = {'mode': 'pongo'}
        with patch('orchard.command.choose', return_value=(adapter, device)), \
             patch('sys.argv', ['orchard', 'boot', str(CLI / 'tests/fixtures/developer/develop.toml')]), \
             patch('sys.stdout', new_callable=io.StringIO):
            main()
        adapter.boot.assert_called_once()
        self.assertEqual(adapter.boot.call_args.args[0], device)
        self.assertEqual(set(adapter.boot.call_args.args[1]), {'pongo', 'recovery'})
        adapter.prepare_mixin.assert_not_called()

    def test_resolve_reads_no_distro_checkout(self):
        inputs = self.root / 'inputs'
        shutil.copytree(FIXTURE, inputs)
        # The tool reads its own checkout's hardware data, never a distro's.
        blocked = [CLI.parent / 'omarchy-a', CLI.parent / 'minimal-linux']
        script = '''
import os, sys
blocked = BLOCKED

def guard(event, args):
    if event == 'open' and args and isinstance(args[0], (str, bytes)):
        path = os.path.abspath(os.fsdecode(args[0]))
        if any(path == root or path.startswith(root + os.sep) for root in blocked):
            raise RuntimeError('Source checkout access: ' + path)

sys.addaudithook(guard)
from orchard.command import main
sys.argv = ['orchard', 'resolve', INPUT, '--profile', 'j71b']
main()
'''.replace('BLOCKED', repr([str(path) for path in blocked])).replace('INPUT', repr(str(inputs / 'index.toml')))
        env = {**os.environ, 'ORCHARD_HOME': str(self.root / 'isolated-store'), 'PYTHONDONTWRITEBYTECODE': '1'}
        env.pop('PYTHONPATH', None)
        process = subprocess.run([sys.executable, '-I', '-c', script], cwd=self.root, env=env,
                                 capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['manifest']['profile'], 'j71b')
        self.assertNotIn(str(CLI.parent / 'omarchy-a'), process.stdout)


if __name__ == '__main__':
    unittest.main()

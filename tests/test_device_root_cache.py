"""Cache protocol and real extraction on temp files; storage/hardware are mocked."""
from contextlib import ExitStack
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

RUNTIME = Path(__file__).resolve().parents[1] / 'runtime/j71b'


def archive(payload=b'public root'):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w:gz') as bundle:
        for name in ('usr', 'usr/lib'):
            member = tarfile.TarInfo(name)
            member.type, member.mode = tarfile.DIRTYPE, 0o755
            member.uid, member.gid = os.getuid(), os.getgid()
            bundle.addfile(member)
        member = tarfile.TarInfo('usr/lib/public.txt')
        member.uid, member.gid = os.getuid(), os.getgid()
        member.mode, member.size = 0o644, len(payload)
        bundle.addfile(member, io.BytesIO(payload))
        member = tarfile.TarInfo('lib')
        member.type, member.linkname = tarfile.SYMTYPE, 'usr/lib'
        member.uid, member.gid = os.getuid(), os.getgid()
        bundle.addfile(member)
    return output.getvalue()


class DeviceCache(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        policy = types.ModuleType('image_policy')
        for name in ('verify_privileges', 'verify_session_access', 'ready', 'validate_root',
                     'validate_handoff', 'diagnostics', 'stop_diagnostics'):
            setattr(policy, name, Mock())
        policy.DIAGNOSTICS_EXECUTABLE = '/unused'
        spec = importlib.util.spec_from_file_location('orchard_device_cache_test', RUNTIME / 'device.py')
        self.device = d = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'image_policy': policy}), \
             patch.object(sys, 'path', [str(RUNTIME), str(RUNTIME.parents[1] / 'apple'), *sys.path]):
            spec.loader.exec_module(d)
        d.ROOT = self.base / 'partition'
        d.ROOT.mkdir()
        d.RUN = self.base / 'run'
        d.PUBLIC_SUMS = self.base / 'public.SHA256SUMS'
        d.PUBLIC_SUMS.write_text('public checksums\n')
        self.partition = {'id': 'data', 'path': '/dev/not-used', 'filesystem': 'ext4',
                          'filesystem_uuid': 'filesystem'}
        d.selected = Mock(return_value=self.partition)
        d.mount_target, d.unmount_target = Mock(), Mock()
        d.command, d.no_faults, d.emit = Mock(), Mock(), Mock()
        self.data = archive()
        self.set_image(self.data)
        self.reset_boot()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        cache_class = d.root_cache.Cache
        self.stack.enter_context(patch.object(d.root_cache, 'Cache',
            side_effect=lambda *args, **kwargs: cache_class(*args, owner_uid=os.getuid(), **kwargs)))
        self.space = types.SimpleNamespace(f_bavail=8 * 1024**3 // 4096, f_frsize=4096, f_favail=100000)
        self.stack.enter_context(patch.object(d.os, 'statvfs', side_effect=lambda _: self.space))

    def set_image(self, data):
        self.data = data
        self.image = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                      'expanded_bytes': 1024, 'members': 4, 'compression': 'gzip'}
        self.release = {'release': 'test-release', 'root': self.image}
        self.device.config = Mock(return_value=self.release)

    def reset_boot(self):
        if self.device.RUN.exists():
            shutil.rmtree(self.device.RUN)
        self.device.RUN.mkdir()
        (self.device.RUN / 'state.json').write_text('{"phase":"prepare","status":"complete"}')
        (self.device.RUN / 'mixin.json').write_text('{"files":{}}')

    def request(self, mode=None, **kwargs):
        d = self.device
        target = d.ROOT / 'omarchy'
        info = target.stat() if target.exists() else None
        result = {'target': 'data', 'directory': 'omarchy', 'selection_token': 'selection',
                  'sha256': self.image['sha256'], 'bytes': self.image['bytes'],
                  'destination_identity': [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]
                                          if info else None, 'replace': info is not None}
        if mode is not None:
            result['root_cache'] = mode
        return {**result, **kwargs}

    def install(self, mode=None, data=None):
        incoming = types.SimpleNamespace(buffer=io.BytesIO(self.data if data is None else data))
        with patch.object(self.device.sys, 'stdin', incoming):
            return self.device.install(self.request(mode))

    def cache_path(self):
        return self.device.ROOT / self.device.root_cache.DIRECTORY / self.device.root_cache.key(self.image)

    def extraction_events(self):
        return [call.kwargs for call in self.device.emit.call_args_list
                if call.args == ('progress',) and call.kwargs.get('phase') == 'cached-extraction']

    def test_cached_extraction_reports_exact_initial_and_completed_bytes(self):
        self.install('store')
        self.assertEqual(self.extraction_events(), [])
        self.reset_boot()
        self.install('reuse')
        events = self.extraction_events()
        self.assertEqual(events[0], {'phase': 'cached-extraction', 'bytes': 0,
                                    'total': len(self.data), 'complete': False})
        self.assertEqual(events[-1], {'phase': 'cached-extraction', 'bytes': len(self.data),
                                     'total': len(self.data), 'complete': True})
        self.assertEqual(sum(event['complete'] for event in events), 1)

    def test_extraction_progress_throttles_and_defers_final_byte_until_success(self):
        d = self.device
        with patch.object(d.time, 'monotonic', side_effect=[10, 10.1, 10.25, 10.3, 10.5]):
            progress = d.ExtractionProgress(100)
            for count in (10, 20, 30, 100):
                progress.advance(count)
        self.assertEqual([event['bytes'] for event in self.extraction_events()], [0, 20])
        self.assertFalse(any(event['complete'] for event in self.extraction_events()))
        progress.finish()
        self.assertEqual([event['bytes'] for event in self.extraction_events()], [0, 20, 100])
        self.assertTrue(self.extraction_events()[-1]['complete'])

    def test_integrity_or_metadata_failure_never_reports_completed_extraction(self):
        d = self.device
        for failure in ('checksum', 'metadata'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                d.emit.reset_mock()
                image = dict(self.image)
                if failure == 'checksum':
                    image['sha256'] = '0' * 64
                with patch.object(d.os, 'symlink', side_effect=OSError('link failed')) if failure == 'metadata' else ExitStack():
                    with self.assertRaises((ValueError, OSError)):
                        d.extract_root(io.BytesIO(self.data), Path(directory), {'root': image}, 'cached-extraction')
                self.assertTrue(self.extraction_events())
                self.assertFalse(any(event['complete'] for event in self.extraction_events()))
                self.assertTrue(all(event['bytes'] < len(self.data) for event in self.extraction_events()))

    def test_counting_reader_progress_is_bounded_and_never_counts_overflow(self):
        reader_class = self.device.extract.__globals__['CountingReader']
        source = Mock()
        source.read.side_effect = [b'abc', b'def', b'']
        progress = Mock()
        reader = reader_class(source, 5, progress)
        self.assertEqual(reader.read(), b'abc')
        with self.assertRaisesRegex(ValueError, 'exceeds declared size'):
            reader.read(2 * 1048576)
        self.assertEqual(progress.call_args_list, [unittest.mock.call(3)])
        self.assertEqual([call.args for call in source.read.call_args_list], [(1048576,), (1048576,)])

    def test_store_then_cold_recovery_replacement_reuses_without_stdin(self):
        d = self.device
        result = self.install('store')
        self.assertEqual(result['root_cache'], 'stored')
        self.assertEqual(self.cache_path().read_bytes(), self.data)
        cached_inode = self.cache_path().stat().st_ino
        self.assertEqual((d.ROOT / 'omarchy/usr/lib/public.txt').read_bytes(), b'public root')
        d.validate_root.assert_called_once()
        d.verify_privileges.assert_called_once()
        d.verify_session_access.assert_called_once()
        self.reset_boot()  # Only RAM state is lost; cache lives on the same ext4 root.
        probe = d.root_cache_status(self.request())
        self.assertEqual(probe, {'cached': True, 'sha256': self.image['sha256'], 'bytes': self.image['bytes']})
        with patch.object(d.sys, 'stdin', object()):
            result = d.install(self.request('reuse'))
        self.assertEqual(result['root_cache'], 'reused')
        self.assertEqual(self.cache_path().stat().st_ino, cached_inode)
        phases = [call.kwargs.get('phase') for call in d.emit.call_args_list]
        self.assertLess(phases.index('cache-hit'), phases.index('deleting-destination'))
        self.assertIn('cached-extraction', phases)
        d.unmount_target.assert_called_with(self.partition)

    @unittest.skipUnless(sys.version_info >= (3, 14), 'stdlib zstd requires Python 3.14')
    def test_zstd_full_transfer_then_cold_cached_extraction(self):
        from compression import zstd
        self.set_image(zstd.compress(gzip.decompress(self.data)))
        self.image['compression'] = 'zstd'
        self.assertEqual(self.install('store')['root_cache'], 'stored')
        self.assertEqual(self.cache_path().read_bytes(), self.data)
        self.reset_boot()
        with patch.object(self.device.sys, 'stdin', object()):
            self.assertEqual(self.device.install(self.request('reuse'))['root_cache'], 'reused')

    def test_old_client_streams_without_touching_even_unsafe_cache_namespace(self):
        directory = self.device.ROOT / self.device.root_cache.DIRECTORY
        directory.symlink_to(self.base)
        result = self.install()
        self.assertNotIn('root_cache', result)
        self.assertTrue(directory.is_symlink())

    def test_corrupted_or_removed_hit_fails_before_deletion_and_marks_no_replacement(self):
        self.install('store')
        self.reset_boot()
        self.cache_path().write_bytes(b'corrupt')
        self.assertFalse(self.device.root_cache_status(self.request())['cached'])
        with patch.object(self.device.shutil, 'rmtree') as delete, \
             self.assertRaisesRegex(ValueError, 'failed verification; nothing was deleted'):
            self.install('reuse')
        delete.assert_not_called()
        self.assertFalse(json.loads((self.device.RUN / 'installation.json').read_text())['replacement_started'])
        self.assertTrue((self.device.ROOT / 'omarchy/usr/lib/public.txt').exists())

    def test_different_build_misses_and_full_transfer_replaces_retained_archive(self):
        self.install('store')
        old = self.cache_path()
        self.reset_boot()
        self.set_image(archive(b'another public build'))
        self.assertFalse(self.device.root_cache_status(self.request())['cached'])
        self.install('store')
        self.assertFalse(old.exists())
        self.assertEqual(self.cache_path().read_bytes(), self.data)

    def test_public_validation_failure_never_finalizes_cache(self):
        self.device.validate_root.side_effect = ValueError('invalid public root')
        with self.assertRaisesRegex(ValueError, 'invalid public root'):
            self.install('store')
        directory = self.device.ROOT / self.device.root_cache.DIRECTORY
        self.assertEqual(list(directory.iterdir()), [])

    def test_private_mixin_is_never_cached_and_reuse_keeps_its_hash_check(self):
        d = self.device
        secret = b'private device firmware, not part of the public archive'
        name = 'lib/firmware/apple/test-private.bin'
        original_open = Path.open
        def opened(path, *args, **kwargs):
            if str(path) == '/' + name:
                return io.BytesIO(secret)
            return original_open(path, *args, **kwargs)
        def mixin(digest):
            (d.RUN / 'mixin.json').write_text(json.dumps({'files': {
                name: {'sha256': digest, 'bytes': len(secret)}}}))
        mixin(hashlib.sha256(secret).hexdigest())
        with patch.object(Path, 'open', opened):
            self.install('store')
        self.assertEqual((d.ROOT / 'omarchy/usr' / name).read_bytes(), secret)
        self.assertEqual(self.cache_path().read_bytes(), self.data)
        with tarfile.open(self.cache_path()) as bundle:
            self.assertNotIn('usr/' + name, bundle.getnames())
        self.reset_boot()
        mixin('0' * 64)
        with patch.object(Path, 'open', opened), self.assertRaisesRegex(ValueError, 'private firmware differs'):
            self.install('reuse')
        self.assertEqual(self.cache_path().read_bytes(), self.data)

    def test_cold_partial_bytes_are_separate_and_cleaned_before_streaming(self):
        d = self.device
        self.install('store')
        self.reset_boot()
        pending = self.cache_path().parent / ('.pending-' + 'a' * 32)
        pending.write_bytes(b'incomplete archive' * 4096)
        pending.chmod(0o600)
        allocated = pending.stat().st_blocks * 512
        self.set_image(archive(b'different public build'))
        result = d.destination(self.request(cache_root=True))
        self.assertEqual(result['cache_reclaimable_bytes'], allocated)
        original_extract = d.extract_root
        def extract_checked(*args, **kwargs):
            self.assertFalse(pending.exists())
            return original_extract(*args, **kwargs)
        with patch.object(d, 'extract_root', side_effect=extract_checked):
            self.install('store')
        self.assertEqual(list(self.cache_path().parent.iterdir()), [self.cache_path()])

    def test_cache_capability_is_not_advertised_by_launcher(self):
        d = self.device
        boot_id = self.base / 'boot-id'
        boot_id.write_text('boot')
        with patch.object(d.identity, 'cached', return_value=None), \
             patch.object(d.ssh_access, 'BOOT_ID', boot_id), \
             patch.object(d.ssh_access, 'revoked', return_value=False), \
             patch.object(d, 'system_ready', return_value=False):
            self.assertIn('root-image-cache-v1', d.status({})['capabilities'])
            self.release['mode'] = 'launcher'
            self.assertNotIn('root-image-cache-v1', d.status({})['capabilities'])

    def test_incorrect_incoming_hash_does_not_finalize_or_leave_partial(self):
        self.image['sha256'] = 'a' * 64
        with self.assertRaisesRegex(ValueError, 'integrity mismatch'):
            self.install('store')
        self.assertEqual(list((self.device.ROOT / self.device.root_cache.DIRECTORY).iterdir()), [])
        self.device.validate_root.assert_not_called()

    def test_probe_is_read_only_and_rejects_wrong_receipt_or_path(self):
        d = self.device
        for changes in ({'sha256': '0' * 64}, {'bytes': True}, {'bytes': self.image['bytes'] + 1},
                        {'directory': '../escape'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                d.root_cache_status(self.request(**changes))
        self.assertFalse(d.root_cache_status(self.request())['cached'])
        d.mount_target.assert_called_once_with(self.partition)
        self.assertFalse((d.ROOT / d.root_cache.DIRECTORY).exists())

    def test_probe_refuses_unsafe_namespace_and_unmounts(self):
        d = self.device
        (d.ROOT / d.root_cache.DIRECTORY).symlink_to(self.base)
        with self.assertRaises((OSError, ValueError)):
            d.root_cache_status(self.request())
        d.unmount_target.assert_called_once_with(self.partition)

    def test_invalid_cache_options_fail_before_writes(self):
        d = self.device
        for mode in (None, True, 1, [], {}, 'other'):
            with self.subTest(mode=mode), self.assertRaisesRegex(ValueError, 'root_cache must'):
                d.install(self.request(root_cache=mode))
        for value in ('yes', 1, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'cache_root must'):
                d.destination(self.request(cache_root=value))
        d.mount_target.assert_not_called()
        self.assertFalse((d.RUN / 'install.started').exists())

    def test_destination_budget_includes_cache_only_on_miss(self):
        d = self.device
        base = d.capacity_needed(self.image)
        uncached = d.destination(self.request(cache_root=True))
        self.assertEqual(uncached['needed_bytes'], base + d.root_cache.allocation(self.image, 4096))
        self.install('store')
        self.reset_boot()
        cached = d.destination(self.request(cache_root=True))
        self.assertEqual(cached['needed_bytes'], base)
        self.assertEqual(cached['cache_reclaimable_bytes'], 0)

    def test_store_reserves_bytes_and_inodes_before_deletion(self):
        d = self.device
        for low_inodes in (False, True):
            with self.subTest(low_inodes=low_inodes):
                if low_inodes:
                    self.space.f_bavail = 8 * 1024**3 // 4096
                    self.space.f_favail = self.image['members'] + 1024 + 1
                else:
                    self.space.f_bavail = (d.capacity_needed(self.image) + 4095) // 4096
                with self.assertRaisesRegex(ValueError, 'Insufficient space or free inodes; nothing was deleted'):
                    self.install('store')
                self.assertFalse((d.RUN / 'install.started').exists())

    def test_writable_mount_rechecks_space_before_deleting_existing_root(self):
        self.install('store')
        self.reset_boot()
        def mounted(partition, writable=False):
            if writable:
                self.space.f_bavail = 0
        self.device.mount_target.side_effect = mounted
        with self.assertRaisesRegex(ValueError, 'nothing was deleted'):
            self.install('reuse')
        self.assertTrue((self.device.ROOT / 'omarchy/usr/lib/public.txt').exists())
        self.assertFalse(json.loads((self.device.RUN / 'installation.json').read_text())['replacement_started'])


if __name__ == '__main__':
    unittest.main()

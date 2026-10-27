"""Public archive cache containment/integrity using unprivileged disk fixtures."""
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


RUNTIME = Path(__file__).resolve().parents[1] / 'runtime/j71b'
spec = importlib.util.spec_from_file_location('orchard_root_cache_test', RUNTIME / 'root_cache.py')
root_cache = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_cache)


def receipt(data):
    return {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}


class RootCache(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / root_cache.DIRECTORY
        self.uid = os.getuid()
        self.data = b'compressed public root archive' * 100
        self.image = receipt(self.data)

    def cache(self, **kwargs):
        return root_cache.Cache(self.root, owner_uid=self.uid, **kwargs)

    def put(self, data=None):
        data = self.data if data is None else data
        image = receipt(data)
        with self.cache(create=True) as cache, cache.store(io.BytesIO(data), image) as writer:
            while writer.read(137):
                pass
            writer.commit()
        return self.directory / root_cache.key(image)

    def snapshot(self):
        if not self.directory.exists():
            return None
        return {entry.name: (entry.read_bytes(), entry.stat().st_ino, entry.stat().st_mode,
                             entry.stat().st_mtime_ns, entry.stat().st_ctime_ns)
                for entry in self.directory.iterdir()}

    def test_absent_probe_does_not_create_directory(self):
        with self.cache() as cache:
            self.assertIsNone(cache.fd)
            self.assertEqual(cache.entries(), {})
            self.assertEqual(cache.pending_usage(), (0, 0))
            with cache.verified(self.image) as stream:
                self.assertIsNone(stream)
        self.assertFalse(self.directory.exists())

    def test_store_cold_reopen_and_read_only_verified_handle(self):
        path = self.put()
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        before = self.snapshot()
        with self.cache() as cache:
            with cache.verified(self.image) as stream:
                self.assertIsNotNone(stream)
                self.assertEqual(stream.tell(), 0)
                self.assertEqual(stream.read(), self.data)
                with self.assertRaises(io.UnsupportedOperation):
                    stream.write(b'no')
            self.assertTrue(stream.closed)
        self.assertEqual(self.snapshot(), before)

    def test_different_exact_hash_or_size_is_a_miss_without_mutation(self):
        self.put()
        before = self.snapshot()
        changed = ({**self.image, 'sha256': 'f' * 64},
                   {**self.image, 'bytes': self.image['bytes'] + 1})
        with self.cache() as cache:
            for image in changed:
                with self.subTest(image=image), cache.verified(image) as stream:
                    self.assertIsNone(stream)
        self.assertEqual(self.snapshot(), before)

    def test_corruption_truncation_and_oversize_are_misses(self):
        path = self.put()
        for content in (b'x' * len(self.data), self.data[:-1], self.data + b'extra', b''):
            with self.subTest(size=len(content)):
                path.write_bytes(content)
                before = self.snapshot()
                with self.cache() as cache, cache.verified(self.image) as stream:
                    self.assertIsNone(stream)
                self.assertEqual(self.snapshot(), before)

    def test_unsafe_directory_permissions_and_owner_fail_closed(self):
        with self.cache(create=True):
            pass
        for mode in (0o755, 0o770, 0o777, 0o1700):
            with self.subTest(mode=oct(mode)):
                self.directory.chmod(mode)
                with self.assertRaisesRegex(ValueError, 'Unsafe root cache directory'):
                    self.cache()
        self.directory.chmod(0o700)
        with self.assertRaisesRegex(ValueError, 'Unsafe root cache directory'):
            root_cache.Cache(self.root, owner_uid=self.uid + 1)

    def test_root_or_cache_directory_symlinks_never_followed(self):
        outside = self.root / 'outside'
        outside.mkdir(mode=0o700)
        protected = outside / 'private'
        protected.write_bytes(b'private mixin must not be read or deleted')
        self.directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            self.cache()
        self.directory.unlink()
        root_alias = self.root / 'alias'
        root_alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((OSError, ValueError)):
            root_cache.Cache(root_alias, owner_uid=self.uid)
        self.assertEqual(protected.read_bytes(), b'private mixin must not be read or deleted')

    def test_unsafe_entries_fail_closed_without_cleanup(self):
        path = self.put()
        for mode in (0o644, 0o666, 0o1600):
            with self.subTest(mode=oct(mode)):
                path.chmod(mode)
                with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
                    self.cache()
        path.chmod(0o600)
        alias = self.root / 'alias'
        os.link(path, alias)
        with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
            self.cache()
        alias.unlink()
        path.unlink()
        path.symlink_to(self.root / 'outside')
        with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
            self.cache()
        self.assertTrue(path.is_symlink())
        path.unlink()
        path.mkdir(mode=0o600)
        with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
            self.cache()
        path.rmdir()
        os.mkfifo(path, 0o600)
        with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
            self.cache()

    def test_verified_handle_rechecks_entry_metadata(self):
        path = self.put()
        with self.cache() as cache:
            path.chmod(0o644)
            with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
                with cache.verified(self.image):
                    self.fail('Unsafe file must not be yielded')
            path.chmod(0o600)
            cache.owner_uid += 1
            with self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
                with cache.verified(self.image):
                    self.fail('Wrong-owner file must not be yielded')

    def test_entry_changed_during_verification_is_a_miss(self):
        path = self.put()
        initial = path.stat()
        original_sha256 = hashlib.sha256
        class ChangingDigest:
            def __init__(self):
                self.digest = original_sha256()

            def update(self, data):
                self.digest.update(data)
                # Even if bytes still hash correctly, changing metadata during
                # verification must prevent reuse of a concurrently changed file.
                os.utime(path, ns=(initial.st_atime_ns, initial.st_mtime_ns + 1000000))

            def hexdigest(self):
                return self.digest.hexdigest()
        with self.cache() as cache, patch.object(root_cache.hashlib, 'sha256', ChangingDigest):
            with cache.verified(self.image) as stream:
                self.assertIsNone(stream)

    def test_file_on_different_filesystem_is_rejected(self):
        path = self.put()
        info = path.stat()
        changed = list(info)
        changed[2] += 1  # st_dev; no privileged fixture mount required.
        with self.cache() as cache, self.assertRaisesRegex(ValueError, 'Unsafe root cache entry'):
            cache.check_file(os.stat_result(changed))

    def test_unknown_names_prevent_any_pending_cleanup(self):
        self.put()
        pending = self.directory / ('.pending-' + 'a' * 32)
        pending.write_bytes(b'interrupted')
        pending.chmod(0o600)
        unknown = self.directory / 'private-firmware'
        unknown.write_bytes(b'never touch')
        unknown.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'Unknown root cache entry'):
            self.cache()
        self.assertEqual(pending.read_bytes(), b'interrupted')
        self.assertEqual(unknown.read_bytes(), b'never touch')

    def test_invalid_key_and_path_inputs_are_rejected(self):
        invalid = ({'sha256': '../outside', 'bytes': 1},
                   {'sha256': 'a' * 64 + '\n', 'bytes': 1},
                   {'sha256': 'A' * 64, 'bytes': 1},
                   {'sha256': 'a' * 63, 'bytes': 1},
                   {'sha256': 123, 'bytes': 1}, {'bytes': 1},
                   {'sha256': 'a' * 64, 'bytes': True},
                   {'sha256': 'a' * 64, 'bytes': 0},
                   {'sha256': 'a' * 64, 'bytes': -1},
                   {'sha256': 'a' * 64, 'bytes': 1.0},
                   {'sha256': 'a' * 64, 'bytes': '1'})
        for image in invalid:
            with self.subTest(image=image), self.assertRaisesRegex(ValueError, 'Invalid trusted'):
                root_cache.key(image)

    def test_invalid_store_receipt_creates_no_partial(self):
        with self.cache(create=True) as cache:
            with self.assertRaisesRegex(ValueError, 'Invalid trusted'):
                with cache.store(io.BytesIO(self.data), {**self.image, 'sha256': '../escape'}):
                    self.fail('Invalid receipt must not yield a writer')
            self.assertEqual(cache.entries(), {})

    def test_namespace_scan_is_bounded(self):
        with self.cache(create=True):
            pass
        for number in range(root_cache.MAX_ENTRIES):
            path = self.directory / ('.pending-%032x' % number)
            path.write_bytes(b'stale')
            path.chmod(0o600)
        with self.cache() as cache:
            self.assertEqual(len(cache.entries()), root_cache.MAX_ENTRIES)
        extra = self.directory / ('.pending-%032x' % root_cache.MAX_ENTRIES)
        extra.write_bytes(b'overflow')
        extra.chmod(0o600)
        with self.assertRaisesRegex(ValueError, 'Too many root cache entries'):
            self.cache()
        self.assertEqual(len(list(self.directory.iterdir())), root_cache.MAX_ENTRIES + 1)

    def test_failed_truncated_wrong_hash_and_oversize_stores_remove_partial(self):
        for content, image, message in ((self.data[:-1], self.image, 'integrity mismatch'),
                                        (b'x' * len(self.data), self.image, 'integrity mismatch'),
                                        (self.data + b'extra', self.image, 'exceeds declared size')):
            with self.subTest(content=len(content)), self.cache(create=True) as cache:
                with self.assertRaisesRegex(ValueError, message):
                    with cache.store(io.BytesIO(content), image) as writer:
                        while writer.read(113):
                            pass
                        writer.commit()
                self.assertEqual(cache.entries(), {})

    def test_uncommitted_and_interrupted_stores_remove_only_partial(self):
        saved = self.put()
        before = saved.read_bytes()
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted), self.cache() as cache:
                try:
                    with cache.store(io.BytesIO(b'next root'), receipt(b'next root')) as writer:
                        writer.read(2)
                        if interrupted:
                            raise KeyboardInterrupt()
                except KeyboardInterrupt:
                    pass
                self.assertEqual(set(cache.entries()), {saved.name})
        self.assertEqual(saved.read_bytes(), before)

    def test_failing_buffered_close_still_removes_partial(self):
        saved = self.put()
        original_fdopen = os.fdopen
        class FailingOutput:
            def __init__(self, *args):
                self.output = original_fdopen(*args)

            def write(self, data):
                if fail_write:
                    raise OSError('simulated write failure')
                return self.output.write(data)

            def close(self):
                self.output.close()
                raise OSError('simulated buffered close failure')
        for fail_write in (False, True):
            with self.subTest(fail_write=fail_write), self.cache() as cache:
                with patch.object(root_cache.os, 'fdopen', FailingOutput), \
                     self.assertRaisesRegex(OSError, 'buffered close failure'):
                    with cache.store(io.BytesIO(b'next image'), receipt(b'next image')) as writer:
                        writer.read(2)
                self.assertEqual(set(cache.entries()), {saved.name})
        self.assertEqual(saved.read_bytes(), self.data)

    def test_exception_after_commit_does_not_publish_new_archive(self):
        saved = self.put()
        other = b'fully transferred but root validation failed'
        with self.cache() as cache:
            with self.assertRaisesRegex(RuntimeError, 'root validation failed'):
                with cache.store(io.BytesIO(other), receipt(other)) as writer:
                    while writer.read():
                        pass
                    writer.commit()
                    raise RuntimeError('root validation failed')
            self.assertEqual(set(cache.entries()), {saved.name})

    def test_cold_partial_usage_and_next_store_cleanup(self):
        saved = self.put()
        pending = self.directory / ('.pending-' + '1' * 32)
        pending.write_bytes(b'interrupted transfer' * 1000)
        pending.chmod(0o600)
        allocated = pending.stat().st_blocks * 512
        with self.cache() as cache:
            self.assertEqual(cache.pending_usage(), (allocated, 1))
            with cache.verified(self.image) as stream:
                self.assertEqual(stream.read(), self.data)
        self.assertTrue(pending.exists())  # Read-only probes do not remove leftovers.
        other = b'new image'
        with self.cache() as cache, cache.store(io.BytesIO(other), receipt(other)) as writer:
            self.assertFalse(pending.exists())
            self.assertTrue(saved.exists())  # Last complete archive retained until commit.
            self.assertEqual(len(cache.entries()), 2)
            while writer.read():
                pass
            writer.commit()
        with self.cache() as cache:
            self.assertEqual(set(cache.entries()), {root_cache.key(receipt(other))})
            self.assertEqual(cache.pending_usage(), (0, 0))
            with cache.verified(receipt(other)) as stream:
                self.assertEqual(stream.read(), other)
            with cache.verified(self.image) as stream:
                self.assertIsNone(stream)

    def test_retention_does_not_touch_installed_root_or_private_siblings(self):
        installed = self.root / 'omarchy'
        installed.mkdir()
        private = installed / 'private-firmware'
        private.write_bytes(b'private device material')
        unrelated = self.root / 'user-data'
        unrelated.write_bytes(b'existing user files')
        self.put()
        self.put(b'new public image')
        self.assertEqual(private.read_bytes(), b'private device material')
        self.assertEqual(unrelated.read_bytes(), b'existing user files')
        self.assertEqual(len(list(self.directory.iterdir())), 1)

    def test_store_requires_explicitly_created_directory(self):
        with self.cache() as cache, self.assertRaisesRegex(ValueError, 'directory is absent'):
            with cache.store(io.BytesIO(self.data), self.image):
                self.fail('Absent cache must not accept storage')

    def test_reads_are_bounded_and_exact_bytes_are_preserved(self):
        data = b'public compressed bytes' * 100000
        source = io.BytesIO(data)
        with self.cache(create=True) as cache, \
             patch.object(source, 'read', wraps=source.read) as read, \
             cache.store(source, receipt(data)) as writer:
            self.assertEqual(len(writer.read(10 ** 9)), 1048576)
            while writer.read():
                pass
            writer.commit()
            self.assertTrue(all(0 <= call.args[0] <= 1048576 for call in read.call_args_list))
        self.assertEqual((self.directory / root_cache.key(receipt(data))).read_bytes(), data)

    def test_allocation_rounds_bytes_up_and_reserves_metadata(self):
        for size, expected in ((1, 3 * 4096), (4096, 3 * 4096), (4097, 4 * 4096)):
            with self.subTest(size=size):
                image = {'sha256': 'a' * 64, 'bytes': size}
                self.assertEqual(root_cache.allocation(image, 4096), expected)

    def test_cold_rename_before_prune_is_bounded_on_next_store(self):
        current = self.put()
        old_data = b'older complete public archive'
        old = self.directory / root_cache.key(receipt(old_data))
        old.write_bytes(old_data)
        old.chmod(0o600)
        os.utime(old, (1, 1))
        # Simulate power loss after publishing a new archive but before pruning
        # the previous one. A new failed transfer must not grow retained history.
        with self.cache() as cache:
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                with cache.store(io.BytesIO(b'new archive'), receipt(b'new archive')) as writer:
                    writer.read()
                    self.assertEqual(len(cache.entries()), 2)  # One complete + one in-flight.
                    raise RuntimeError('interrupted')
        self.assertEqual(list(self.directory.iterdir()), [current])


if __name__ == '__main__':
    unittest.main()

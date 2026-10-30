"""File-backed Git acquisition tests. No network or device access."""
import os
from pathlib import Path
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import patch

from orchard_cli import sources

import hardware_layout


class SourceCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.upstream = self.root / 'upstream'
        self.upstream.mkdir()
        self.cache = self.root / 'cache'
        self.git('init', '-q')
        (self.upstream / 'driver.c').write_text('original\n')
        (self.upstream / 'untouched.c').write_text('unchanged\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.commit = self.git('rev-parse', 'HEAD').decode()
        self.url = self.upstream.as_uri()

    def git(self, *args):
        return sources.git(self.upstream, *args)

    def test_two_checkouts_share_objects_without_contacting_upstream_again(self):
        first, second = self.root / 'first', self.root / 'second'
        sources.checkout(self.url, self.commit, first, cache=self.cache)
        # Make the original URL unavailable: the second checkout must be offline.
        self.upstream.rename(self.root / 'upstream-offline')
        sources.checkout(self.url, self.commit, second, cache=self.cache)
        self.assertEqual((first / 'driver.c').read_text(), 'original\n')
        self.assertEqual((second / 'driver.c').read_text(), 'original\n')
        self.assertEqual((first / '.git/objects/info/alternates').read_text(),
                         (second / '.git/objects/info/alternates').read_text())
        (first / 'driver.c').write_text('local edit\n')
        self.assertEqual((second / 'driver.c').read_text(), 'original\n')
        with self.assertRaises(ValueError):
            sources.checkout(self.url, self.commit, first, cache=self.cache)
        self.assertEqual((first / 'driver.c').read_text(), 'local edit\n')

    def test_declared_tree_matches_patched_checkout_and_preserves_user_index(self):
        patchfile = self.root / 'change.patch'
        (self.upstream / 'driver.c').write_text('patched\n')
        patchfile.write_bytes(self.git('diff', '--binary') + b'\n')
        self.git('checkout', '--', 'driver.c')
        tree = sources.declared_tree(self.url, self.commit, [patchfile], cache=self.cache)
        local = self.root / 'local'
        sources.checkout(self.url, self.commit, local, cache=self.cache)
        sources.git(local, 'apply', patchfile)
        index_before = (local / '.git/index').read_bytes()
        self.assertEqual(tree, sources.working_tree(local))
        self.assertEqual(index_before, (local / '.git/index').read_bytes())
        (local / 'new.c').write_text('untracked\n')
        self.assertNotEqual(tree, sources.working_tree(local))

    def test_local_checkout_can_seed_cache_without_remote_access(self):
        missing_url = (self.root / 'missing-origin').as_uri()
        with sources.repository(missing_url, self.commit, cache=self.cache, donor=self.upstream) as bare:
            self.assertEqual(sources.git(bare, 'rev-parse', self.commit).decode(), self.commit)

    def test_cli_prepares_source_for_unmodified_hardware_builder_without_resetting_edits(self):
        from orchard_cli import kernel
        hardware = self.root / 'hardware'
        patches = hardware_layout.write(hardware)
        (hardware / 'kernel/upstream.lock').write_text(f'url={self.url}\ncommit={self.commit}\n')
        (self.upstream / 'driver.c').write_text('patched\n')
        patchfile = patches / '0001-driver.patch'
        patchfile.write_bytes(self.git('diff', '--binary') + b'\n')
        self.git('checkout', '--', 'driver.c')
        source, output = kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'patched\n')
        self.assertTrue(output.is_dir())
        self.assertTrue((source.parent / 'applied-patches').is_file())
        (source / 'driver.c').write_text('local experiment\n')
        kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'local experiment\n')
        patchfile.write_bytes(patchfile.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'local experiment\n')

    def test_patch_transition_uses_central_cache_then_retains_incremental_output(self):
        from orchard_cli import build, kernel
        hardware = self.root / 'hardware'
        patches = hardware_layout.write(hardware)
        (hardware / 'kernel/upstream.lock').write_text(f'url={self.url}\ncommit={self.commit}\n')
        patchfile = patches / '0001-driver.patch'
        def change(text):
            (self.upstream / 'driver.c').write_text(text)
            patchfile.write_bytes(self.git('diff', '--binary') + b'\n')
            self.git('checkout', '--', 'driver.c')
        change('first patch\n')
        source, output = kernel.prepare_source(hardware, self.cache, 'j71b')
        (output / 'retained.o').write_bytes(b'compiled object')
        # Legacy clean workspaces gain a baseline even on a cache-only lookup.
        (source.parent / 'prepared-tree').unlink()
        build.kernel_base_key(hardware, 'j71b', self.cache)
        self.assertEqual((source.parent / 'prepared-tree').read_text().strip(),
                         sources.working_tree(source))
        untouched = (source / 'untouched.c').stat().st_mtime_ns
        index = (source / '.git/index').read_bytes()
        change('second patch\n')
        # Another checkout has already published this exact content-addressed base.
        other = self.root / 'other'
        shutil.copytree(hardware, other, ignore=shutil.ignore_patterns('.work'))
        expected = build.kernel_base_key(other, 'j71b', self.cache)
        component_cache = self.cache / 'components-test'
        cached = build.cached_component(component_cache, 'kernel-base', lambda: expected,
                                        lambda stage: (stage / 'Image.gz').write_bytes(b'cached kernel'))
        with patch.object(kernel, 'prepare_source', side_effect=AssertionError('cache hit rebuilt source')):
            actual = build.cached_component(component_cache, 'kernel-base',
                lambda: build.kernel_base_key(hardware, 'j71b', self.cache),
                lambda stage: self.fail('central cache hit invoked the compiler'))
        self.assertEqual(actual, cached)
        self.assertEqual((source / 'driver.c').read_text(), 'first patch\n')
        # On a miss, reconcile only the declared delta; keep compiler artifacts.
        kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'second patch\n')
        self.assertEqual((source / 'untouched.c').stat().st_mtime_ns, untouched)
        self.assertEqual((source / '.git/index').read_bytes(), index)
        self.assertEqual((output / 'retained.o').read_bytes(), b'compiled object')
        self.assertEqual(build.kernel_base_key(hardware, 'j71b', self.cache), expected)
        # Removing patches is also a declared transition, not a clean rebuild.
        patchfile.unlink()
        kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'original\n')
        self.assertEqual((output / 'retained.o').read_bytes(), b'compiled object')
        self.assertEqual((source / 'untouched.c').stat().st_mtime_ns, untouched)

    def test_patch_transitions_preserve_blank_context_and_binary_patch_bytes(self):
        from orchard_cli import kernel
        cases = ((b'first\n\n', b'second\n\n'),
                 (b'first\n   \n', b'second\n   \n'),
                 (b'\x00first\n', b'\x00second\n'))
        for number, (first, second) in enumerate(cases):
            with self.subTest(number=number):
                hardware = self.root / ('hardware-' + str(number))
                patches = hardware_layout.write(hardware)
                (hardware / 'kernel/upstream.lock').write_text(f'url={self.url}\ncommit={self.commit}\n')
                def change(content):
                    (self.upstream / 'driver.c').write_bytes(content)
                    raw = subprocess.check_output(['git', '-C', str(self.upstream), 'diff', '--binary'])
                    (patches / 'driver.patch').write_bytes(raw)
                    self.git('checkout', '--', 'driver.c')
                change(first)
                source, output = kernel.prepare_source(hardware, self.cache, 'j71b')
                (output / 'keep.o').write_bytes(b'compiled object')
                change(second)
                kernel.prepare_source(hardware, self.cache, 'j71b')
                self.assertEqual((source / 'driver.c').read_bytes(), second)
                self.assertEqual((output / 'keep.o').read_bytes(), b'compiled object')

    def test_transition_from_a_series_built_outside_orchard(self):
        # hardware/kernel/build run directly leaves the shared tree at a series
        # the CLI never declared; the next transition must still find its objects.
        from orchard_cli import kernel
        hardware = self.root / 'hardware'
        patches = hardware_layout.write(hardware)
        (hardware / 'kernel/upstream.lock').write_text(f'url={self.url}\ncommit={self.commit}\n')
        def change(text, name='0001-driver.patch'):
            (self.upstream / 'driver.c').write_text(text)
            (patches / name).write_bytes(self.git('diff', '--binary') + b'\n')
            self.git('checkout', '--', 'driver.c')
        change('first patch\n')
        source, output = kernel.prepare_source(hardware, self.cache, 'j71b')
        (source / 'driver.c').write_text('series built by kernel/build alone\n')
        work = source.parent
        (work / 'applied-patches').write_text('recorded by kernel/build\n')
        (work / 'prepared-tree').write_text(sources.working_tree(source) + '\n')
        change('second patch\n')
        kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'second patch\n')

    def test_changed_patches_with_user_edits_cannot_hit_unrelated_cache(self):
        from orchard_cli import build, kernel
        hardware = self.root / 'hardware'
        patches = hardware_layout.write(hardware)
        (hardware / 'kernel/upstream.lock').write_text(f'url={self.url}\ncommit={self.commit}\n')
        source, output = kernel.prepare_source(hardware, self.cache, 'j71b')
        (source / 'driver.c').write_text('user edit\n')
        sources.git(source, 'add', 'driver.c')
        index = (source / '.git/index').read_bytes()
        (self.upstream / 'driver.c').write_text('new patch\n')
        (patches / 'new.patch').write_bytes(self.git('diff', '--binary') + b'\n')
        with self.assertRaisesRegex(ValueError, 'refusing to discard edits'):
            build.kernel_base_key(hardware, 'j71b', self.cache)
        with self.assertRaises(ValueError):
            kernel.prepare_source(hardware, self.cache, 'j71b')
        self.assertEqual((source / 'driver.c').read_text(), 'user edit\n')
        self.assertEqual((source / '.git/index').read_bytes(), index)

    def test_cache_location_override(self):
        with patch.dict(os.environ, {'ORCHARD_CACHE_DIR': str(self.cache)}):
            self.assertEqual(sources.cache_root(), self.cache)


if __name__ == '__main__':
    unittest.main()

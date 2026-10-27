"""Offline P105 source preparation contracts using synthetic Git inputs."""
import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    'p105_prepare_test', str(ROOT / 'kernel/p105/prepare-source'))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
prepare = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(prepare)


class P105ConfigMerge(unittest.TestCase):
    def test_fragment_replaces_every_old_definition_and_preserves_other_symbols(self):
        base = ('# Original configuration\nCONFIG_KEEP="unchanged"\n'
                '# CONFIG_NAMESPACES is not set\nCONFIG_NAMESPACES=n\n'
                'CONFIG_FUSE_FS=n\nCONFIG_PREEMPT=y\n')
        fragment = 'CONFIG_NAMESPACES=y\nCONFIG_FUSE_FS=y\n# CONFIG_PREEMPT is not set\n'
        result = prepare.merge_config(base, fragment)
        self.assertEqual(result.count('CONFIG_NAMESPACES'), 1)
        self.assertEqual(result.count('CONFIG_FUSE_FS'), 1)
        self.assertEqual(result.count('CONFIG_PREEMPT'), 1)
        self.assertIn('CONFIG_KEEP="unchanged"\n', result)
        self.assertIn('# Original configuration\n', result)
        self.assertIn('CONFIG_NAMESPACES=y\n', result)
        self.assertIn('CONFIG_FUSE_FS=y\n', result)
        self.assertIn('# CONFIG_PREEMPT is not set\n', result)

    def test_repeated_merge_is_identical_and_accepts_a_changed_fragment(self):
        base = '# Upstream\nCONFIG_KEEP=y\n# CONFIG_FUSE_FS is not set\n'
        first = prepare.merge_config(base, 'CONFIG_FUSE_FS=y\n')
        self.assertEqual(prepare.merge_config(first, 'CONFIG_FUSE_FS=y\n'), first)
        replacement = prepare.merge_config(first, '# CONFIG_FUSE_FS is not set\n')
        self.assertEqual(replacement.count('CONFIG_FUSE_FS'), 1)
        self.assertIn('CONFIG_KEEP=y\n', replacement)
        self.assertIn('# CONFIG_FUSE_FS is not set\n', replacement)
        self.assertEqual(prepare.merge_config(replacement, '# CONFIG_FUSE_FS is not set\n'), replacement)

    def test_private_initramfs_path_or_an_empty_fragment_is_refused(self):
        for fragment in ('CONFIG_INITRAMFS_SOURCE="/private/owner/initramfs"\n',
                         '# CONFIG_INITRAMFS_SOURCE is not set\n',
                         '# No selected symbols\n', ''):
            with self.subTest(fragment=fragment), self.assertRaises(ValueError):
                prepare.merge_config('CONFIG_KEEP=y\n', fragment)


@unittest.skipUnless(shutil.which('git') and shutil.which('awk'), 'Git and awk are required')
class P105SourcePreparation(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='p105-source-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repository = self.root / 'upstream'
        self.hardware = self.root / 'hardware'
        self.here = self.hardware / 'kernel/p105'
        self.repository.mkdir()
        self.here.mkdir(parents=True)
        # Keep fixture commits independent of the operator's hooks and signing.
        environment = patch.dict(os.environ, {
            'GIT_CONFIG_GLOBAL': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
            'GIT_AUTHOR_NAME': 'P105 Test Fixture',
            'GIT_AUTHOR_EMAIL': 'p105-fixture@example.invalid',
            'GIT_COMMITTER_NAME': 'P105 Test Fixture',
            'GIT_COMMITTER_EMAIL': 'p105-fixture@example.invalid',
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.reference = b'SYNTHETIC-REFERENCE-CALIBRATION\x00\x01\x02'
        files = {
            '.gitignore': b'build/\n',
            'README.txt': b'Synthetic public Cascadia source\n',
            'config/p105ap.config': (b'CONFIG_KEEP=y\n# CONFIG_NAMESPACES is not set\n'
                                    b'CONFIG_NAMESPACES=n\nCONFIG_FUSE_FS=n\n'),
            'scripts/check-config-fragment.awk': (
                b'/^(# )?CONFIG_/ { line=$0; sub(/^# /,"",line); '
                b'split(line,p,/=| /); if (seen[p[1]]++) exit 1 }\n'),
            prepare.EXCLUDED[0]: self.reference,
        }
        targets = ('handoff', 'home', 'calibration-gate', 'native-container')
        for name in targets:
            files['fixture/' + name + '.txt'] = ('before-' + name + '\n').encode()
        for name, data in files.items():
            path = self.repository / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'commit.gpgsign=false', 'commit', '-qm', 'Synthetic source fixture')
        self.commit = self.git('rev-parse', 'HEAD').strip()
        self.lock = {
            'cascadia_url': 'https://github.com/rzxvx/Project-Cascadia.git',
            'cascadia_commit': self.commit,
            'linux_url': 'https://github.com/torvalds/linux.git',
            'linux_commit': '1' * 40, 'linux_tag': 'v6.12',
        }
        patch_paths = [self.hardware / name for name in prepare.RUNTIME_PATCHES]
        patch_paths += [self.here / 'private-calibration.patch', self.here / 'native-container.patch']
        for path, name in zip(patch_paths, targets):
            path.parent.mkdir(parents=True, exist_ok=True)
            relative = 'fixture/' + name + '.txt'
            path.write_text('diff --git a/{0} b/{0}\n--- a/{0}\n+++ b/{0}\n'
                            '@@ -1 +1 @@\n-before-{1}\n+after-{1}\n'.format(relative, name))
        (self.here / 'config.fragment').write_text('CONFIG_NAMESPACES=y\nCONFIG_FUSE_FS=y\n')

    def git(self, *arguments):
        return subprocess.check_output(['git', '-C', str(self.repository), *arguments],
                                       text=True, stderr=subprocess.PIPE)

    @contextlib.contextmanager
    def resources(self):
        with patch.multiple(prepare, HERE=self.here, HARDWARE=self.hardware), \
             patch.object(prepare, 'pins', return_value=dict(self.lock)):
            yield

    def run_prepare(self, output, source=None):
        args = argparse.Namespace(source=self.repository if source is None else source, output=output)
        with self.resources(), contextlib.redirect_stdout(io.StringIO()):
            prepare.prepare(args)

    def assert_refused_before_export(self, output, message, source=None):
        with patch.object(prepare, 'export_source') as export:
            with self.assertRaisesRegex(ValueError, message):
                self.run_prepare(output, source)
            export.assert_not_called()

    def test_clean_source_exports_only_public_tracked_files_and_applies_resources(self):
        # These bytes belong to the synthetic fixture. No real calibration is read.
        (self.repository / prepare.EXCLUDED[0]).write_bytes(b'SYNTHETIC-LOCAL-REFERENCE-EDIT')
        private = self.repository / 'build/keep/lib/firmware/mtcal.bin'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'SYNTHETIC-OWNER-INPUT')
        output = self.root / 'private-output'
        archived = {}
        original_export = prepare.export_source

        def inspect_archive(repository, commit, paths, destination, archive_path):
            original_export(repository, commit, paths, destination, archive_path)
            archived['bytes'] = archive_path.read_bytes()
            with tarfile.open(archive_path, 'r:') as archive:
                archived['names'] = archive.getnames()

        with patch.object(prepare, 'export_source', side_effect=inspect_archive):
            self.run_prepare(output)
        self.assertEqual((output / 'README.txt').read_text(), 'Synthetic public Cascadia source\n')
        for name in ('handoff', 'home', 'calibration-gate', 'native-container'):
            self.assertEqual((output / 'fixture' / (name + '.txt')).read_text(), 'after-' + name + '\n')
        self.assertFalse((output / '.git').exists())
        self.assertFalse((output / 'build').exists())
        self.assertFalse((output / prepare.EXCLUDED[0]).exists())
        self.assertNotIn(prepare.EXCLUDED[0], archived['names'])
        for value in (self.reference, b'SYNTHETIC-LOCAL-REFERENCE-EDIT', b'SYNTHETIC-OWNER-INPUT'):
            self.assertNotIn(value, archived['bytes'])
        config = (output / 'config/p105ap.config').read_text()
        self.assertIn('CONFIG_KEEP=y\n', config)
        self.assertEqual(config.count('CONFIG_NAMESPACES'), 1)
        self.assertIn('CONFIG_NAMESPACES=y\n', config)
        self.assertIn('CONFIG_FUSE_FS=y\n', config)
        receipt = json.loads((output / 'orchard-p105-source.json').read_text())
        self.assertEqual(receipt['upstream']['cascadia_commit'], self.commit)
        self.assertEqual(receipt['excluded_tracked_paths'], list(prepare.EXCLUDED))
        self.assertIs(receipt['private_build_outputs'], True)
        self.assertEqual(len(receipt['patches']), 4)

    def test_wrong_commit_is_refused_before_export_or_output_creation(self):
        self.lock['cascadia_commit'] = '0' * 40
        output = self.root / 'new-parent/output'
        self.assert_refused_before_export(output, 'revision differs')
        self.assertFalse(output.parent.exists())

    def test_dirty_public_tracked_source_is_refused_before_output_creation(self):
        (self.repository / 'README.txt').write_text('Uncommitted public source edit\n')
        output = self.root / 'new-parent/output'
        self.assert_refused_before_export(output, 'modified tracked source')
        self.assertFalse(output.parent.exists())

    def test_untracked_nonignored_source_is_refused_before_output_creation(self):
        (self.repository / 'unexpected-private-input').write_bytes(b'SYNTHETIC-UNTRACKED')
        output = self.root / 'new-parent/output'
        self.assert_refused_before_export(output, 'untracked non-ignored')
        self.assertFalse(output.parent.exists())

    def test_existing_outputs_are_preserved_including_a_dangling_symlink(self):
        directory = self.root / 'existing-directory'
        directory.mkdir()
        sentinel = directory / 'keep.txt'
        sentinel.write_bytes(b'preserve directory')
        file_path = self.root / 'existing-file'
        file_path.write_bytes(b'preserve file')
        link = self.root / 'dangling-output'
        link.symlink_to('absent-target')
        for output in (directory, file_path, link):
            with self.subTest(output=output.name):
                self.assert_refused_before_export(output, 'fresh output')
        self.assertEqual(sentinel.read_bytes(), b'preserve directory')
        self.assertEqual(file_path.read_bytes(), b'preserve file')
        self.assertTrue(link.is_symlink())
        self.assertEqual(os.readlink(link), 'absent-target')

    def test_output_inside_hardware_or_input_checkout_is_refused(self):
        for output, message in ((self.hardware / 'private-build', 'outside the hardware'),
                                (self.repository / 'private-build', 'outside the input')):
            with self.subTest(output=output.parent.name):
                self.assert_refused_before_export(output, message)
                self.assertFalse(output.exists())

    def test_output_alias_into_hardware_checkout_is_refused(self):
        alias = self.root / 'hardware-alias'
        alias.symlink_to(self.hardware, target_is_directory=True)
        output = alias / 'private-build'
        self.assert_refused_before_export(output, 'outside the hardware')
        self.assertFalse((self.hardware / 'private-build').exists())

    def test_source_must_be_a_checkout_root(self):
        output = self.root / 'new-parent/output'
        self.assert_refused_before_export(output, 'checkout root', self.repository / 'fixture')
        self.assertFalse(output.parent.exists())


if __name__ == '__main__':
    unittest.main()

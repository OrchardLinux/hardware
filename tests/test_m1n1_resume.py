"""Verify an overlapping loader patch stack without changing its checkout."""
import difflib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class M1n1Resume(unittest.TestCase):
    def setUp(self):
        if shutil.which('git') is None or shutil.which('bash') is None:
            self.skipTest('Git and Bash are required for loader resume checks')
        temporary = tempfile.TemporaryDirectory(prefix='orchard-m1n1-resume-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'work/source'
        (self.source / 'src').mkdir(parents=True)
        self.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_CONFIG_NOSYSTEM='1', GIT_AUTHOR_NAME='Orchard test',
                        GIT_AUTHOR_EMAIL='test@example.invalid',
                        GIT_COMMITTER_NAME='Orchard test',
                        GIT_COMMITTER_EMAIL='test@example.invalid')
        self.git('init', '--quiet')
        base = 'before\ncalibration = touch;\nafter\n'
        first = 'before\ncalibration = touch_blob;\nafter\n'
        second = 'before\ncalibration = touch_and_prox_blobs;\nafter\n'
        (self.source / 'src/kboot.c').write_text(base)
        (self.source / 'tracked.txt').write_text('committed\n')
        (self.source / '.gitignore').write_text('build/\n')
        self.git('add', '--all')
        self.git('commit', '--quiet', '-m', 'Fixture upstream')
        commit = self.git('rev-parse', 'HEAD').stdout.strip()
        loader = self.root / 'loader'
        patches = loader / 'patches'
        (patches / 'hoolock').mkdir(parents=True)
        (patches / 'orchard').mkdir()
        (loader / 'series').write_text('patches/hoolock\npatches/orchard\n')
        self.build = loader / 'build'
        shutil.copyfile(ROOT / 'boot/m1n1/build', self.build)
        (loader / 'upstream.lock').write_text(
            'url=https://example.invalid/unused.git\ncommit=' + commit + '\n')
        self.first_patch = patches / 'hoolock/0001-calibration.patch'
        self.first_patch.write_text(''.join(difflib.unified_diff(
            base.splitlines(True), first.splitlines(True),
            fromfile='a/src/kboot.c', tofile='b/src/kboot.c')) +
            ''.join(difflib.unified_diff([], ['touch calibration\n'],
                fromfile='/dev/null', tofile='b/src/calibration.c')))
        second_patch = patches / 'orchard/0002-proximity.patch'
        second_patch.write_text(''.join(difflib.unified_diff(
            first.splitlines(True), second.splitlines(True),
            fromfile='a/src/kboot.c', tofile='b/src/kboot.c')))
        for patch in (self.first_patch, second_patch):
            self.git('apply', str(patch))
        applied = []
        for patch in (self.first_patch, second_patch):
            blob = subprocess.check_output(['git', 'hash-object', str(patch)], text=True).strip()
            applied.append(blob + ' ' + patch.relative_to(loader).as_posix() + '\n')
        (self.source.parent / 'applied-patches').write_text(''.join(applied))

        # The real index deliberately differs from both HEAD and the work file.
        (self.source / 'tracked.txt').write_text('staged\n')
        self.git('add', 'tracked.txt')
        (self.source / 'tracked.txt').write_text('working\n')
        (self.source / 'untracked.txt').write_text('keep this work\n')
        (self.source / 'build').mkdir()
        (self.source / 'build/m1n1.bin').write_bytes(b'fixture loader\0')
        self.output = self.root / 'out/m1n1.bin'
        self.log = self.root / 'buildenv.jsonl'
        temporary_indexes = self.root / 'temporary-indexes'
        temporary_indexes.mkdir()
        mocks = self.root / 'mocks'
        package = mocks / 'orchard_hardware'
        package.mkdir(parents=True)
        (package / '__init__.py').write_text('')
        (package / 'buildenv.py').write_text(
            'import json, os, sys\n'
            'with open(os.environ["ORCHARD_TEST_BUILDENV_LOG"], "a") as log:\n'
            '    log.write(json.dumps(sys.argv[1:]) + "\\n")\n')
        self.env.update(PYTHONPATH=str(mocks), TMPDIR=str(temporary_indexes),
                        ORCHARD_TEST_BUILDENV_LOG=str(self.log))

    def git(self, *args, check=True):
        return subprocess.run(['git', '-C', str(self.source), *args],
                              check=check, capture_output=True, text=True,
                              env=self.env)

    def snapshot(self):
        files = {str(path.relative_to(self.source)): path.read_bytes()
                 for path in self.source.rglob('*')
                 if path.is_file() and '.git' not in path.relative_to(self.source).parts}
        return (files, (self.source / '.git/index').read_bytes())

    def resume(self):
        return subprocess.run(['bash', str(self.build), 'n61', '--resume',
                               '--work-dir', str(self.source.parent),
                               '--output', str(self.output)],
                              capture_output=True, text=True, env=self.env)

    def test_overlapping_stack_resumes_and_preserves_real_index_and_files(self):
        independent = self.git('apply', '--reverse', '--check', str(self.first_patch), check=False)
        self.assertNotEqual(independent.returncode, 0)
        before = self.snapshot()
        completed = self.resume()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.git('show', ':tracked.txt').stdout, 'staged\n')
        self.assertEqual(self.output.read_bytes(), b'fixture loader\0')
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([call[0] for call in calls], ['build', 'run'])
        self.assertEqual(list(Path(self.env['TMPDIR']).glob('orchard-m1n1-resume.*')), [])

    def test_incomplete_earlier_patch_refuses_build_and_discards_temporary_index(self):
        # Reversing patch 2 can succeed before patch 1 discovers this loss.
        (self.source / 'src/calibration.c').unlink()
        before = self.snapshot()
        completed = self.resume()
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn('src/calibration.c', completed.stderr)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.output.exists())
        self.assertFalse(self.log.exists())
        self.assertEqual(list(Path(self.env['TMPDIR']).glob('orchard-m1n1-resume.*')), [])


if __name__ == '__main__':
    unittest.main()

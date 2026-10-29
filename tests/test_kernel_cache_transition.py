"""Exercise incremental kernel cache transitions with a small Git fixture."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class KernelCacheTransition(unittest.TestCase):
    def setUp(self):
        if shutil.which('git') is None or shutil.which('bash') is None:
            self.skipTest('Git and bash are required')
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.hardware = root / 'hardware'
        kernel = self.hardware / 'kernel'
        kernel.mkdir(parents=True)
        shutil.copyfile(ROOT / 'kernel/build', kernel / 'build')
        shutil.copyfile(ROOT / 'kernel/series.py', kernel / 'series.py')
        (kernel / 'write-receipt.py').write_text('raise SystemExit(42)\n')
        config = kernel / 'configs/apple-4k/config'
        config.parent.mkdir(parents=True)
        config.write_text('config\n')
        series = kernel / 'series/apple-4k'
        series.parent.mkdir()
        series.write_text('patches/devices\n')
        descriptor = self.hardware / 'devices/n61/device.toml'
        descriptor.parent.mkdir(parents=True)
        descriptor.write_text('[kernel]\nplatform = "apple-4k"\ndtb = "t7000-n61"\n')
        self.patch = kernel / 'patches/devices/0001.patch'
        self.patch.parent.mkdir(parents=True)
        self.patch.write_text(self.diff('old'))

        self.work = root / 'work'
        self.source = self.work / 'source'
        self.source.mkdir(parents=True)
        self.git('init', '-q')
        self.git('remote', 'add', 'origin', 'https://github.com/torvalds/linux.git')
        (self.source / 'driver.txt').write_text('base\n')
        (self.source / 'editable.txt').write_text('original\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Orchard test', '-c', 'user.email=test@example.test',
                 'commit', '-qm', 'kernel fixture')
        commit = self.git('rev-parse', 'HEAD').strip()
        (kernel / 'upstream.lock').write_text(
            'url=https://github.com/torvalds/linux.git\ncommit=' + commit + '\n')
        self.git('apply', str(self.patch))
        self.git('add', '.')
        tree = self.git('write-tree').strip()
        self.git('reset', '-q', 'HEAD')
        (self.work / 'prepared-tree').write_text(tree + '\n')
        blob = subprocess.check_output(['git', 'hash-object', str(self.patch)], text=True).strip()
        (self.work / 'applied-patches').write_text(blob + ' patches/devices/0001.patch\n')
        self.output = self.work / 'output'
        self.output.mkdir()
        (self.output / 'compiler-output.o').write_bytes(b'original compiler output')

    @staticmethod
    def diff(value):
        return ('diff --git a/driver.txt b/driver.txt\n'
                '--- a/driver.txt\n+++ b/driver.txt\n@@ -1 +1 @@\n'
                '-base\n+' + value + '\n')

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.source), *args], text=True)

    def run_build(self):
        env = dict(os.environ, ORCHARD_BUILD_NATIVE='1')
        return subprocess.run(['bash', str(self.hardware / 'kernel/build'), 'n61',
                               '--work-dir', str(self.work), '--output',
                               str(self.hardware / 'result')], env=env,
                              capture_output=True, text=True)

    def test_patch_change_keeps_other_source_and_compiler_output(self):
        self.patch.write_text(self.diff('new'))
        result = self.run_build()
        # The fake receipt writer stops the test after the cache transition.
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('applying only the difference', result.stdout)
        self.assertEqual((self.source / 'driver.txt').read_text(), 'new\n')
        self.assertEqual((self.source / 'editable.txt').read_text(), 'original\n')
        self.assertEqual((self.output / 'compiler-output.o').read_bytes(), b'original compiler output')

    def test_patch_change_preserves_hand_edits_and_refuses_transition(self):
        self.patch.write_text(self.diff('new'))
        (self.source / 'editable.txt').write_text('manual change\n')
        result = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('changes that are not in the patch series', result.stderr)
        self.assertEqual((self.source / 'driver.txt').read_text(), 'old\n')
        self.assertEqual((self.source / 'editable.txt').read_text(), 'manual change\n')
        self.assertEqual((self.output / 'compiler-output.o').read_bytes(), b'original compiler output')


if __name__ == '__main__':
    unittest.main()

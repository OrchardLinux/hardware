"""Local and CI execution use identical recipe commands; never access a device."""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

from orchard_hardware import buildenv


class BuildEnvironment(unittest.TestCase):
    def test_local_execution_preserves_container_flags_and_command(self):
        with patch.dict(os.environ, {'ORCHARD_BUILD_NATIVE': '0'}), \
             patch.object(buildenv.subprocess, 'run') as run:
            buildenv.build('builder', Path('Containerfile'), Path('.'), arch='arm64')
            self.assertEqual(run.call_args.args[0][:4], ['podman', 'build', '--arch', 'arm64'])
            buildenv.run('builder', ['make', '-j2'], [(Path('.'), '/src', True)],
                         env={'FLAG': 'value'}, cwd='/src', root=True, arch='arm64')
            argv = run.call_args.args[0]
            self.assertIn('keep-id:uid=0,gid=0', argv)
            self.assertIn('none', argv)
            self.assertIn(str(Path('.').resolve()) + ':/src:ro', argv)
            self.assertIn('FLAG=value', argv)
            self.assertIn('ORCHARD_SRC=/src', argv)
            self.assertEqual(argv[-3:], ['builder', 'make', '-j2'])

    def test_native_execution_uses_real_paths_not_podman(self):
        with patch.dict(os.environ, {'ORCHARD_BUILD_NATIVE': '1', 'ORCHARD_BUILD_ENV_ID': 'test-toolchain'}), \
             patch.object(buildenv.platform, 'system', return_value='Linux'), \
             patch.object(buildenv.platform, 'machine', return_value='aarch64'), \
             patch.object(buildenv.subprocess, 'run') as run:
            buildenv.build('builder', Path('Containerfile'), Path('.'))
            run.assert_not_called()
            buildenv.run('builder', ['make', '-j2'], [(Path('.'), '/src', True)],
                         env={'FLAG': 'value'}, cwd='/src')
            argv = run.call_args.args[0]
            self.assertEqual(argv, ['make', '-j2'])
            self.assertEqual(run.call_args.kwargs['cwd'], str(Path('.').resolve()))
            self.assertEqual(run.call_args.kwargs['env']['ORCHARD_SRC'], str(Path('.').resolve()))
            self.assertEqual(run.call_args.kwargs['env']['FLAG'], 'value')
            self.assertEqual(buildenv.identity('builder'), 'native:test-toolchain')

    def test_native_builds_reject_unprovisioned_or_wrong_hosts(self):
        with patch.dict(os.environ, {'ORCHARD_BUILD_NATIVE': '1', 'ORCHARD_BUILD_ENV_ID': ''}), \
             patch.object(buildenv.platform, 'system', return_value='Linux'), \
             patch.object(buildenv.platform, 'machine', return_value='aarch64'):
            with self.assertRaisesRegex(ValueError, 'ENV_ID'):
                buildenv.native()
        with patch.dict(os.environ, {'ORCHARD_BUILD_NATIVE': '1', 'ORCHARD_BUILD_ENV_ID': 'test'}), \
             patch.object(buildenv.platform, 'system', return_value='Darwin'):
            with self.assertRaisesRegex(ValueError, 'Linux ARM64'):
                buildenv.native()
        with patch.dict(os.environ, {'ORCHARD_BUILD_NATIVE': 'yes'}):
            with self.assertRaisesRegex(ValueError, '0 or 1'):
                buildenv.native()

    def test_native_temporary_paths_are_retired_after_failure(self):
        paths = []
        def failed(command, **kwargs):
            paths.extend(Path(kwargs['env'][name]) for name in ('ORCHARD_WORK', 'ORCHARD_TMP'))
            self.assertTrue(all(path.is_dir() for path in paths))
            raise subprocess.CalledProcessError(1, command)
        with patch.object(buildenv, 'native', return_value=True), \
             patch.object(buildenv.subprocess, 'run', side_effect=failed):
            with self.assertRaises(subprocess.CalledProcessError):
                buildenv.run('builder', ['make'])
        self.assertTrue(all(not path.exists() for path in paths))

    def test_argument_and_environment_translation_respect_path_boundaries(self):
        mounts = [(Path('.'), '/src', True)]
        self.assertEqual(buildenv.translate('/src/recipe', mounts), str(Path('.').resolve()) + '/recipe')
        self.assertEqual(buildenv.translate('/src-other/recipe', mounts), '/src-other/recipe')
        self.assertEqual(buildenv.translate('make -C /src', mounts), 'make -C /src')


if __name__ == '__main__':
    unittest.main()

"""bin/orchard picks the orchard checkout to run; fake uv/git, no environment setup."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

LAUNCHER = Path(__file__).resolve().parents[1] / 'bin/orchard'


class LauncherTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'uv.log'
        self.git_log = self.root / 'git.log'
        self.cache = self.root / 'cache'
        self.write_executable('uv', 'printf "%s\\n" "$UV_PROJECT_ENVIRONMENT" "$@" > "$LAUNCH_LOG"\n')
        # git clone URL DEST makes an empty checkout; everything else is logged only.
        self.write_executable('git', 'printf "%s\\n" "$*" >> "$GIT_LOG"\n'
                              'if [ "$1" = clone ]; then mkdir -p "$4/.git"; fi\n')

    def write_executable(self, name, body):
        path = self.bin / name
        path.write_text('#!/bin/sh\nset -eu\n' + body)
        path.chmod(0o755)

    def checkout(self, path):
        path.mkdir(parents=True)
        (path / 'pyproject.toml').write_text('[project]\nname = "orchard"\nversion = "0"\n')
        return path

    def launch(self, cwd, *arguments):
        return subprocess.run(['sh', str(LAUNCHER), *arguments], cwd=cwd,
                              env={'PATH': f'{self.bin}:/usr/bin:/bin', 'HOME': str(self.root),
                                   'LAUNCH_LOG': str(self.log), 'GIT_LOG': str(self.git_log),
                                   'ORCHARD_CACHE_DIR': str(self.cache),
                                   'UV_PROJECT_ENVIRONMENT': '/some/other/environment'},
                              capture_output=True, text=True)

    def launched(self):
        return self.log.read_text().splitlines()

    def test_inside_a_checkout_runs_that_checkout(self):
        orchard = self.checkout(self.root / 'orchard-gpu')
        (orchard / 'examples/minimal-boot').mkdir(parents=True)
        result = self.launch(orchard / 'examples/minimal-boot', 'build', 'with spaces', '--modules')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched(), [str(orchard / '.venv'), 'run', '--quiet', '--project', str(orchard),
                                           'orchard', 'build', 'with spaces', '--modules'])
        self.assertFalse(self.git_log.exists())

    def test_local_file_names_the_checkout_relative_to_itself(self):
        orchard = self.checkout(self.root / 'orchard-installer')
        distro = self.root / 'omarchy-a'
        (distro / 'release').mkdir(parents=True)
        (distro / 'orchard.local.toml').write_text('device = "j71b"\norchard = "../orchard-installer"\n')
        result = self.launch(distro / 'release', 'checkout')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(orchard))

    def test_local_file_naming_no_checkout_stops(self):
        distro = self.root / 'omarchy-a'
        distro.mkdir()
        (distro / 'orchard.local.toml').write_text('orchard = "../missing"\n')
        result = self.launch(distro, 'build')
        self.assertEqual(result.returncode, 1)
        self.assertIn('not an orchard checkout', result.stderr)
        self.assertFalse(self.log.exists())

    def test_without_a_checkout_runs_mainline_from_the_cache(self):
        distro = self.root / 'minimal-linux'
        distro.mkdir()
        (distro / 'orchard.local.toml').write_text('device = "j71b"\n')  # names no checkout
        result = self.launch(distro, 'build')
        self.assertEqual(result.returncode, 0, result.stderr)
        main = self.cache / 'orchard-main'
        self.assertEqual(self.git_log.read_text().splitlines(),
                         [f'clone --quiet https://github.com/OrchardLinux/orchard.git {main}'])
        self.assertEqual(self.launched()[:5], [str(main / '.venv'), 'run', '--quiet', '--project', str(main)])

    def test_mainline_fetched_recently_is_not_fetched_again(self):
        main = self.cache / 'orchard-main'
        (main / '.git').mkdir(parents=True)
        (main / '.git/FETCH_HEAD').write_text('')
        result = self.launch(self.root, 'checkout')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), str(main))
        self.assertFalse(self.git_log.exists())

    def test_python_runs_in_the_checkout_environment(self):
        orchard = self.checkout(self.root / 'orchard')
        result = self.launch(orchard, 'python', '-m', 'unittest')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.launched(), [str(orchard / '.venv'), 'run', '--quiet', '--project', str(orchard),
                                           'python', '-m', 'unittest'])

    def test_exit_status_is_preserved(self):
        orchard = self.checkout(self.root / 'orchard')
        self.write_executable('uv', 'exit 7\n')
        self.assertEqual(self.launch(orchard, '--version').returncode, 7)


class ExampleTests(unittest.TestCase):
    """Examples teach the system; they must parse and name no board."""
    EXAMPLES = sorted((Path(__file__).resolve().parents[1] / 'examples').iterdir())

    def test_every_example_has_a_valid_init_and_payload(self):
        from orchard import build
        self.assertTrue(self.EXAMPLES)
        for example in self.EXAMPLES:
            with self.subTest(example=example.name):
                init = example / 'init'
                self.assertTrue(os.access(init, os.X_OK), 'init must be executable')
                subprocess.run(['sh', '-n', str(init)], check=True)
                build.payload_settings(example)
                self.assertNotIn('apple,j71b', init.read_text())
                self.assertFalse((example / 'orchard.toml').exists())


if __name__ == '__main__':
    unittest.main()

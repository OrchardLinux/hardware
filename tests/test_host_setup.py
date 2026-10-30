"""Host setup never invokes device operations or replaces existing boot tools."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard import host_setup


class HostSetup(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.palera = self.root / 'palera'
        self.palera.write_bytes(b'palera fixture')

    def which(self, name):
        local = self.root / '.local/bin' / name
        if local.exists():
            return str(local)
        return '/usr/bin/' + name if name != 'palera1n' else None

    def test_linux_installs_missing_tools_and_rules_only_once(self):
        rule = self.root / 'rules'
        def run(argv, **kwargs):
            if argv[1] == 'install':
                rule.write_text(Path(argv[-2]).read_text())
        with patch.object(host_setup.platform, 'system', return_value='Linux'), \
             patch.object(host_setup.platform, 'machine', return_value='x86_64'), \
             patch.object(host_setup.os, 'geteuid', return_value=1000), \
             patch.object(host_setup.Path, 'home', return_value=self.root), \
             patch.object(host_setup, 'library_path'), \
             patch.object(host_setup, 'USB_RULES', rule), \
             patch.object(host_setup.shutil, 'which', side_effect=self.which), \
             patch.object(host_setup, 'asset', side_effect=[self.palera]) as download, \
             patch.object(host_setup.subprocess, 'run', side_effect=run) as commands:
            first = host_setup.setup(Mock())
            second = host_setup.setup(Mock())
        self.assertFalse(first['device_modified'])
        self.assertEqual(first, second)
        self.assertEqual(download.call_count, 1)
        self.assertEqual(commands.call_count, 2)  # install rules, reload; never palera/device commands.
        self.assertEqual(rule.read_text().count('MODE="0660"'), 4)
        self.assertEqual((self.root / '.local/bin/palera1n').read_bytes(), b'palera fixture')

    def test_macos_keeps_existing_tools_and_never_uses_sudo(self):
        with patch.object(host_setup.platform, 'system', return_value='Darwin'), \
             patch.object(host_setup.platform, 'machine', return_value='arm64'), \
             patch.object(host_setup.os, 'geteuid', return_value=1000), \
             patch.object(host_setup.Path, 'home', return_value=self.root), \
             patch.object(host_setup, 'library_path'), \
             patch.object(host_setup.shutil, 'which', side_effect=lambda name: '/existing/' + name), \
             patch.object(host_setup, 'asset') as download, \
             patch.object(host_setup.subprocess, 'run') as commands:
            result = host_setup.setup(Mock())
        download.assert_not_called()
        commands.assert_not_called()
        self.assertEqual(result['tools']['palera1n'], '/existing/palera1n')

    def test_tool_publication_refuses_existing_destination(self):
        target = self.root / 'existing'
        target.write_bytes(b'keep')
        with self.assertRaises(ValueError):
            host_setup.install_tool(self.palera, target)
        self.assertEqual(target.read_bytes(), b'keep')

    def test_release_asset_requires_digest(self):
        row = {'name': 'binary', 'digest': None}
        with patch.object(host_setup, 'open_url', return_value=io.BytesIO(json.dumps({'assets': [row]}).encode())), \
             patch.object(host_setup, 'materialize') as download:
            with self.assertRaises(ValueError):
                host_setup.asset('org/repo', 'v1', 'binary', Mock())
        download.assert_not_called()

    def test_release_asset_uses_verified_artifact_cache(self):
        row = {'name': 'binary', 'digest': 'sha256:' + hashlib.sha256(b'bytes').hexdigest(),
               'size': 5, 'browser_download_url': 'https://github.com/org/repo/releases/download/v1/binary'}
        with patch.object(host_setup, 'open_url', return_value=io.BytesIO(json.dumps({'assets': [row]}).encode())), \
             patch.object(host_setup, 'materialize', return_value=self.palera) as download:
            self.assertEqual(host_setup.asset('org/repo', 'v1', 'binary', Mock()), self.palera)
        self.assertEqual(download.call_args.args[0]['sha256'], row['digest'][7:])



class Requirements(unittest.TestCase):
    """orchard setup/doctor: one list of requirements, shared with setup-host."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tools = {'ssh', 'ssh-keygen', 'git', 'podman', 'glu'}
        self.ran = []
        mainline = self.root / 'cache/orchard-main'
        def run(argv, **kwargs):
            self.ran.append(list(map(str, argv)))
            if argv[:2] == ['git', 'clone']:
                (mainline / 'bin').mkdir(parents=True)
                (mainline / '.git').mkdir()
                (mainline / 'bin/orchard').write_text('launcher')
            if argv[:3] == ['podman', 'machine', 'inspect']:
                return subprocess.CompletedProcess(argv, 0, 'running\n', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        import subprocess
        self.mainline = mainline
        for target in (patch.object(host_setup.platform, 'system', return_value='Darwin'),
                       patch.object(host_setup.platform, 'machine', return_value='arm64'),
                       patch.object(host_setup.os, 'geteuid', return_value=1000),
                       patch.object(host_setup.Path, 'home', return_value=self.root),
                       patch.object(host_setup, 'library_path'),
                       patch.object(host_setup.shutil, 'which', side_effect=lambda name: f'/bin/{name}' if name in self.tools else None),
                       patch.object(host_setup.subprocess, 'run', side_effect=run),
                       patch.dict(os.environ, {'PATH': str(self.root / '.local/bin'), 'ORCHARD_CACHE_DIR': str(self.root / 'cache')}),
                       patch.object(host_setup, 'GLU', str(self.root / 'no-glu')),
                       patch.object(host_setup, 'asset', return_value=self.palera())):
            target.start()
            self.addCleanup(target.stop)

    def palera(self):
        path = self.root / 'palera-download'
        path.write_bytes(b'palera')
        return path

    def test_glu_comes_before_homebrew_and_neither_is_installed_for_you(self):
        self.assertEqual(host_setup.package_manager(), ('glu', ['/bin/glu', 'install', '--yes']))
        self.tools = {'brew'}
        self.assertEqual(host_setup.package_manager(), ('brew', ['/bin/brew', 'install']))
        self.tools = set()
        self.assertIsNone(host_setup.package_manager())
        apply, detail = host_setup._install('libusb')
        self.assertIsNone(apply)
        self.assertIn('install glu or Homebrew', detail)

    def test_linux_package_names_follow_the_package_manager(self):
        with patch.object(host_setup.platform, 'system', return_value='Linux'):
            for manager, tool, package in (('apt-get', 'apt', 'libusb-1.0-0'), ('dnf', 'dnf', 'libusb1'),
                                           ('pacman', 'pacman', 'libusb')):
                with self.subTest(manager=manager):
                    self.tools = {manager}
                    name, argv = host_setup.package_manager()
                    self.assertEqual((name, argv[0]), (tool, 'sudo'))
                    self.assertEqual(host_setup.PACKAGES['libusb'][name], package)

    def test_setup_says_what_it_will_do_asks_and_is_safe_to_rerun(self):
        shown = []
        questions = []
        status = host_setup.setup_interactive(output=shown.append, ask=lambda q: questions.append(q) or 'y',
                                              interactive=True)
        self.assertEqual(status, 0)
        self.assertIn('[todo ] palera1n', shown[0])
        self.assertIn('[todo ] orchard command', shown[0])
        self.assertEqual(questions, ['Do the 2 steps marked "todo"? [Y/n] '])
        self.assertEqual(os.readlink(self.root / '.local/bin/orchard'), str(self.mainline / 'bin/orchard'))
        self.assertEqual((self.root / '.local/bin/palera1n').read_bytes(), b'palera')
        ran = len(self.ran)
        questions.clear()
        self.assertEqual(host_setup.setup_interactive(output=shown.append, ask=questions.append, interactive=True), 0)
        self.assertEqual(questions, [])  # nothing left to ask about
        self.assertEqual(shown[-1], 'Ready.')
        self.assertFalse([argv for argv in self.ran[ran:] if argv[:2] == ['git', 'clone']])

    def test_declining_or_no_terminal_changes_nothing(self):
        self.assertEqual(host_setup.setup_interactive(output=lambda text: None, ask=lambda q: 'n', interactive=True), 1)
        with self.assertRaisesRegex(ValueError, '--yes'):
            host_setup.setup_interactive(output=lambda text: None, interactive=False)
        self.assertFalse((self.root / '.local/bin/orchard').exists())

    def test_a_failed_step_says_to_rerun(self):
        self.tools.discard('podman')
        def fail(argv, **kwargs):
            raise host_setup.subprocess.CalledProcessError(1, argv)
        with patch.object(host_setup.subprocess, 'run', side_effect=fail):
            with self.assertRaisesRegex(ValueError, '(?s)podman failed.*rerun setup'):
                host_setup.setup_interactive(output=lambda text: None, yes=True)

    def test_an_existing_orchard_file_is_never_replaced(self):
        (self.root / '.local/bin').mkdir(parents=True)
        (self.root / '.local/bin/orchard').write_text('someone else')
        step = host_setup._launcher_step()
        self.assertIsNone(step.apply)
        self.assertIn('move it away', step.detail)

    def test_the_installer_path_uses_the_same_requirements_without_a_package_manager(self):
        with patch.object(host_setup, 'library_path', side_effect=OSError('no libusb')):
            with self.assertRaisesRegex(ValueError, 'libusb-1.0 is unavailable: install with glu: libusb'):
                host_setup.setup(Mock())
        result = host_setup.setup(Mock())
        self.assertEqual(result['tools']['palera1n'], str(self.root / '.local/bin/palera1n'))
        self.assertFalse([argv for argv in self.ran if '/bin/glu' in argv])


if __name__ == '__main__':
    unittest.main()

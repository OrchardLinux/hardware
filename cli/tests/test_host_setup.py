"""Host setup never invokes device operations or replaces existing boot tools."""
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard_cli import host_setup


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


if __name__ == '__main__':
    unittest.main()

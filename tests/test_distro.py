"""A distribution checkout as a project: distro.toml, orchard build, the last build. No devices."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from orchard import distro

FIXTURE = Path(__file__).resolve().parent / 'fixtures/first'

# A build entry that writes the fixture release under the version it is given.
ENTRY = f'''
import argparse, hashlib, shutil
from pathlib import Path
parser = argparse.ArgumentParser()
parser.add_argument('--version', required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
shutil.copytree({str(FIXTURE)!r}, args.output)
release = args.output / 'release.toml'
release.write_text(release.read_text().replace('version = "example-1"', f'version = "{{args.version}}"'))
data = release.read_bytes()
(args.output / 'index.toml').write_text(
    'schema = 2\\nkind = "orchard.index"\\ndistro = "fixture-linux"\\n\\n[release]\\nfile = "release.toml"\\n'
    f'bytes = {{len(data)}}\\nsha256 = "{{hashlib.sha256(data).hexdigest()}}"\\n')
'''


class DistributionProject(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / '.git').mkdir()
        (self.root / 'tools').mkdir()
        (self.root / 'tools/build.py').write_text(ENTRY)
        (self.root / 'distro.toml').write_text('name = "fixture-linux"\nbuild = "tools/build.py"\n')

    def test_found_from_a_subdirectory_but_not_beyond_the_repository(self):
        (self.root / 'desktop').mkdir()
        self.assertEqual(distro.find(self.root / 'desktop'), self.root.resolve())
        inner = self.root / 'nested'
        (inner / '.git').mkdir(parents=True)
        self.assertIsNone(distro.find(inner))

    def test_settings_refuse_paths_outside_and_unknown_keys(self):
        for text in ('name = "fixture-linux"\nbuild = "../build"\n',
                     'name = "fixture-linux"\nbuild = "/bin/sh"\n',
                     'name = "Fixture Linux"\nbuild = "tools/build.py"\n',
                     'name = "fixture-linux"\nbuild = "tools/build.py"\ndevice = "j71b"\n',
                     'name = "fixture-linux"\nbuild = "tools/missing"\n'):
            with self.subTest(text=text):
                (self.root / 'distro.toml').write_text(text)
                with self.assertRaises(ValueError):
                    distro.settings(self.root)

    def test_a_shell_entry_must_be_executable(self):
        (self.root / 'tools/build').write_text('#!/bin/sh\n')
        (self.root / 'distro.toml').write_text('name = "fixture-linux"\nbuild = "tools/build"\n')
        with self.assertRaisesRegex(ValueError, 'not executable'):
            distro.settings(self.root)
        (self.root / 'tools/build').chmod(0o755)
        self.assertEqual(distro.settings(self.root)['build'], self.root / 'tools/build')

    def test_build_checks_the_release_and_records_it(self):
        index = distro.build(self.root, version='dev-1')
        self.assertEqual(index, self.root.resolve() / 'out/releases/dev-1/index.toml')
        self.assertEqual(distro.latest(self.root), index)
        record = json.loads((self.root / 'out/releases/latest.json').read_text())
        self.assertEqual(record, {'version': 'dev-1', 'index': 'dev-1/index.toml'})
        with self.assertRaisesRegex(ValueError, 'exists'):
            distro.build(self.root, version='dev-1')

    def test_the_release_must_be_this_distribution_and_version(self):
        (self.root / 'distro.toml').write_text('name = "other-linux"\nbuild = "tools/build.py"\n')
        with self.assertRaisesRegex(ValueError, "names 'other-linux'"):
            distro.build(self.root, version='dev-1')
        (self.root / 'distro.toml').write_text('name = "fixture-linux"\nbuild = "tools/build.py"\n')
        (self.root / 'tools/build.py').write_text(ENTRY.replace('f\'version = "{args.version}"\'', "'version = \"x\"'"))
        with self.assertRaisesRegex(ValueError, "version 'x'"):
            distro.build(self.root, version='dev-2')
        with self.assertRaisesRegex(ValueError, 'No build'):
            distro.latest(self.root)

    def test_a_failing_entry_records_nothing(self):
        (self.root / 'tools/build.py').write_text('raise SystemExit(3)\n')
        with self.assertRaises(subprocess.CalledProcessError):
            distro.build(self.root, version='dev-1')
        self.assertFalse((self.root / 'out/releases/latest.json').exists())

    def test_default_versions_are_new_and_valid(self):
        import datetime
        moment = datetime.datetime(2026, 10, 31, 9, 5, 7, tzinfo=datetime.timezone.utc)
        self.assertEqual(distro.default_version(moment), 'dev-20261031-090507')
        with self.assertRaisesRegex(ValueError, 'A version'):
            distro.build(self.root, version='../escape')

    def test_run_boots_the_recovery_of_the_attached_board_without_installing(self):
        distro.build(self.root, version='dev-1')
        adapter, device = mock.Mock(PROFILE='j71b'), {'mode': 'dfu'}
        with mock.patch('orchard.devices.choose', return_value=(adapter, device)), \
                mock.patch('orchard.state.operation_lock'):
            result = distro.run(self.root, build_first=False, yes=True)
        self.assertEqual(result['release'], 'dev-1')
        (_, assets, record, _), _ = adapter.boot.call_args
        self.assertEqual(set(assets), {'pongo', 'recovery'})
        self.assertEqual(record['platform'], 'apple-16k')


class KeyedComponents(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root)
        (self.root / '.git').mkdir()
        (self.root / 'tools').mkdir()
        (self.root / 'tools/build.py').write_text('')
        (self.root / 'distro.toml').write_text('name = "fixture-linux"\nbuild = "tools/build.py"\n')
        (self.root / 'desktop').mkdir()
        (self.root / 'desktop/shell.qml').write_text('one')
        self.home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.home)
        patcher = mock.patch.dict(os.environ, {'ORCHARD_CACHE_DIR': str(self.home)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.calls = []

    def produce(self, path, work):
        self.calls.append(work)
        self.assertFalse(path.exists())
        self.assertTrue(work.is_dir())
        path.write_text(f'built {len(self.calls)}')

    def build(self, **options):
        return distro.component(self.root, 'desktop', ['desktop'], self.produce, artifact='payload', **options)

    def test_same_inputs_reuse_and_changed_inputs_rebuild(self):
        first, key = self.build()
        self.assertEqual(first.read_text(), 'built 1')
        self.assertEqual(self.build(), (first, key))
        self.assertEqual(len(self.calls), 1)
        (self.root / 'desktop/shell.qml').write_text('two')
        second, other = self.build()
        self.assertNotEqual(key, other)
        self.assertEqual(second.read_text(), 'built 2')
        self.assertEqual(self.build(extra=['dependency changed'])[0].read_text(), 'built 3')

    def test_only_the_newest_results_stay(self):
        paths = []
        for text in ('a', 'b', 'c'):
            (self.root / 'desktop/shell.qml').write_text(text)
            paths.append(self.build()[0])
        self.assertEqual([path.exists() for path in paths], [False, True, True])

    def test_a_failed_build_leaves_no_result(self):
        def fail(path, work):
            path.write_text('half')
            raise RuntimeError('compiler crashed')
        with self.assertRaises(RuntimeError):
            distro.component(self.root, 'desktop', ['desktop'], fail, artifact='payload')
        self.assertEqual(self.build()[0].read_text(), 'built 1')


if __name__ == '__main__':
    unittest.main()

"""orchard's core: standard library only, packed as a zipapp, installed as the user's orchard. No devices."""
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

from orchard import installer
from orchard.cache import STATE_VERSION, check_state

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tests/fixtures/first'
# Development fallbacks the core never reaches: launch builds loaders only
# when none are packaged, devices reads orchard.local.toml only without a
# profile, host setup links mainline only in `orchard setup`.
# compression.zstd is the standard library from Python 3.14 on (install_device, on the recovery).
STANDARD = sys.stdlib_module_names | {'compression'}
FALLBACKS = {('orchard.launch', 'orchard.build'), ('orchard.devices', 'orchard.build'),
             ('orchard.host_setup', 'orchard.sources')}


def module_name(archive_path):
    parts = Path(archive_path).with_suffix('').parts[1:]
    return '.'.join(parts[:-1] if parts[-1] == '__init__' else parts)


class StandardLibraryOnly(unittest.TestCase):
    def test_the_core_imports_only_the_standard_library_and_itself(self):
        files = installer.core_files()
        core = {module_name(name) for name in files}
        self.assertIn('orchard.user', core)
        self.assertIn('orchard.service', core)
        self.assertNotIn('orchard.build', core)
        for name, path in files.items():
            module = module_name(name)
            package = module if path.name == '__init__.py' else module.rpartition('.')[0]
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = package.split('.')[:len(package.split('.')) - node.level + 1] if node.level else []
                    prefix = '.'.join(base + ([node.module] if node.module else []))
                    targets = [prefix] if node.module else [f'{prefix}.{alias.name}' for alias in node.names]
                else:
                    continue
                for target in targets:
                    top = target.split('.')[0]
                    with self.subTest(module=module, imports=target):
                        if top != 'orchard':
                            self.assertIn(top, STANDARD | {'jsonschema'} if target == 'jsonschema' else STANDARD)
                        elif (module, target) not in FALLBACKS:
                            self.assertTrue(target in core or target.rpartition('.')[0] in core, target)

    def test_jsonschema_is_only_ever_optional(self):
        for name, path in installer.core_files().items():
            text = path.read_text()
            if 'jsonschema' in text:
                with self.subTest(module=name):
                    self.assertNotIn('\nfrom jsonschema', text)
                    self.assertNotIn('\nimport jsonschema', text)


class Store(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.home)
        patcher = mock.patch.dict(os.environ, {'ORCHARD_HOME': str(self.home / 'store'), 'HOME': str(self.home),
                                               'PATH': '/usr/bin:/bin'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.loaders = {}
        for name in ('pongo', 'm1n1'):
            self.loaders[name] = self.home / f'{name}.bin'
            self.loaders[name].write_bytes(name.encode())

    def run_core(self, archive, *arguments):
        environment = {**os.environ, 'HOME': str(self.home)}
        return subprocess.run([sys.executable, '-I', '-S', str(archive), *arguments], capture_output=True,
                              text=True, env=environment)


class Zipapp(Store):
    def test_runs_without_site_packages_and_unpacks_once(self):
        archive = self.home / 'orchard-core.pyz'
        receipt = installer.build(archive, loaders=self.loaders, boards=['j71b'])
        self.assertEqual(receipt['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
        result = self.run_core(archive, 'version')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), receipt['version'])
        cores = list((self.home / 'store/core').iterdir())
        self.assertEqual([path.name for path in cores], [f'{receipt["version"]}-{receipt["sha256"][:12]}'])
        self.assertEqual((cores[0] / 'loaders/m1n1.bin').read_bytes(), b'm1n1')
        self.assertTrue((cores[0] / 'devices/j71b/device.toml').is_file())
        self.assertFalse((cores[0] / 'src/orchard/build.py').exists())
        self.assertEqual(json.loads((self.home / 'store/state.json').read_text()), {'version': STATE_VERSION})
        # Every module imports without site-packages (no jsonschema); install_device runs on the recovery.
        modules = sorted(module_name(name) for name in installer.core_files()
                         if not name.endswith('install_device.py'))
        imported = subprocess.run([sys.executable, '-I', '-S', '-c',
                                   'import importlib, sys; sys.path.insert(0, sys.argv[1]); '
                                   '[importlib.import_module(name) for name in sys.argv[2:]]',
                                   str(cores[0] / 'src'), *modules], capture_output=True, text=True)
        self.assertEqual(imported.returncode, 0, imported.stderr)
        # Without package metadata the core knows its version from core.json (release checks need it).
        known = subprocess.run([sys.executable, '-I', '-S', '-c',
                                'import sys; sys.path.insert(0, sys.argv[1]); import orchard; print(orchard.version())',
                                str(cores[0] / 'src')], capture_output=True, text=True)
        self.assertEqual(known.stdout.strip(), receipt['version'], known.stderr)

    def test_the_same_inputs_give_the_same_zipapp(self):
        first = installer.build(self.home / 'a.pyz', loaders=self.loaders, boards=['j71b'])
        second = installer.build(self.home / 'b.pyz', loaders=self.loaders, boards=['j71b'])
        self.assertEqual(first['sha256'], second['sha256'])

    def test_a_release_gets_its_installer_and_install_sh(self):
        release = self.home / 'release'
        shutil.copytree(FIXTURE, release)
        receipt = installer.release(release, loaders=self.loaders)
        script = (release / 'install.sh').read_text()
        self.assertIn(f'INSTALLER_SHA256={receipt["sha256"]}', script)
        self.assertIn('INSTALLER_URL=\n', script)
        subprocess.run(['sh', '-n', str(release / 'install.sh')], check=True)
        with zipfile.ZipFile(release / 'orchard-install.pyz') as archive:
            names = archive.namelist()
            self.assertEqual(archive.read('release/index.toml'), (release / 'index.toml').read_bytes())
        self.assertIn('release/release.toml', names)
        self.assertIn('devices/j71b/device.toml', names)
        from orchard.user import bundled_release
        core = self.home / 'core'
        (core / 'release').mkdir(parents=True)
        shutil.copy(release / 'index.toml', core / 'release/index.toml')
        self.assertEqual(bundled_release(core, release / 'orchard-install.pyz'), release / 'index.toml')
        self.assertEqual(bundled_release(core, self.home / 'elsewhere.pyz'), core / 'release/index.toml')

    def test_publishing_rewrites_the_installer_for_the_release_urls(self):
        from orchard.image.publish import publish
        release = self.home / 'release'
        release.mkdir()
        for path in (FIXTURE / 'fixtures').iterdir():
            shutil.copy(path, release / path.name)
        # The fixture release, flat as publish needs it, as a distro publish knows.
        manifest = (FIXTURE / 'release.toml').read_text().replace('file = "fixtures/', 'file = "')
        (release / 'release.toml').write_text(manifest.replace('"fixture-linux"', '"minimal-linux"'))
        data = (release / 'release.toml').read_bytes()
        (release / 'index.toml').write_text(
            'schema = 2\nkind = "orchard.index"\ndistro = "minimal-linux"\n\n[release]\nfile = "release.toml"\n'
            f'bytes = {len(data)}\nsha256 = "{hashlib.sha256(data).hexdigest()}"\n')
        installer.release(release, loaders=self.loaders)
        published = publish(release, 'v1', self.home / 'published', distro='minimal-linux').parent
        base = 'https://github.com/OrchardLinux/minimal-linux/releases/download/v1/'
        script = (published / 'install.sh').read_text()
        self.assertIn(f'INSTALLER_URL={base}orchard-install.pyz', script)
        self.assertIn(f'INSTALLER_SHA256={hashlib.sha256((published / "orchard-install.pyz").read_bytes()).hexdigest()}',
                      script)
        with zipfile.ZipFile(published / 'orchard-install.pyz') as archive:
            self.assertEqual(archive.read('release/index.toml'), (published / 'index.toml').read_bytes())
            self.assertNotIn('release/release.toml', archive.namelist())  # fetched by URL, checked by hash
            self.assertEqual(archive.read('loaders/m1n1.bin'), b'm1n1')
        self.assertIn(f'"url" = "{base}release.toml"', (published / 'index.toml').read_text())

    def test_a_published_install_sh_downloads_the_installer(self):
        text = installer.script(distro='omarchy-a', name='Omarchy A', installer_sha256='0' * 64,
                                installer_url='https://github.com/OrchardLinux/omarchy-a/releases/download/v1/orchard-install.pyz')
        self.assertIn('INSTALLER_URL=https://github.com/OrchardLinux/omarchy-a/releases/download/v1/', text)
        for host, (target, size, checksum) in installer.PYTHON['builds'].items():
            self.assertIn(f'{host}) target={target} bytes={size} sha256={checksum}', text)


class UserOrchard(Store):
    def core(self, version, name=None):
        core = self.home / 'store/core' / (name or version)
        core.mkdir(parents=True)
        (core / 'core.json').write_text(json.dumps({'version': version}))
        return core

    def test_adopts_only_a_newer_core(self):
        older, newer = self.core('0.2.0'), self.core('0.10.0')
        self.assertTrue(installer.adopt(older, '/py', output=lambda _: None))
        launcher = self.home / '.local/bin/orchard'
        self.assertIn(installer.MARKER, launcher.read_text())
        self.assertIn(str(older / 'src'), launcher.read_text())
        self.assertTrue(installer.adopt(newer, '/py', output=lambda _: None))
        self.assertFalse(installer.adopt(older, '/py', output=lambda _: None))
        self.assertFalse(installer.adopt(newer, '/py', output=lambda _: None))
        self.assertEqual(installer.current()['version'], '0.10.0')
        self.assertIn(str(newer / 'src'), launcher.read_text())

    def test_a_rebuilt_core_of_the_same_version_takes_over(self):
        first, rebuilt = self.core('0.2.0', '0.2.0-a'), self.core('0.2.0', '0.2.0-b')
        self.assertTrue(installer.adopt(first, '/py', output=lambda _: None))
        self.assertTrue(installer.adopt(rebuilt, '/py', output=lambda _: None))
        self.assertIn(str(rebuilt / 'src'), (self.home / '.local/bin/orchard').read_text())

    def test_palera1n_in_local_bin_counts_off_path(self):
        from orchard import host_setup
        binary = self.home / '.local/bin/palera1n'
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text('#!/bin/sh\n')
        binary.chmod(0o755)
        with mock.patch.dict(os.environ, {'PATH': '/usr/bin:/bin'}), \
                mock.patch.object(Path, 'home', return_value=self.home):
            self.assertEqual(host_setup.palera1n(), str(binary))

    def test_a_development_orchard_on_path_stays(self):
        launcher = self.home / '.local/bin/orchard'
        launcher.parent.mkdir(parents=True)
        launcher.symlink_to('/somewhere/orchard-main/bin/orchard')
        messages = []
        self.assertTrue(installer.adopt(self.core('0.2.0'), '/py', output=messages.append))
        self.assertEqual(os.readlink(launcher), '/somewhere/orchard-main/bin/orchard')
        self.assertIn('development orchard', messages[0])

    def test_the_launcher_runs_the_core(self):
        archive = self.home / 'orchard-core.pyz'
        receipt = installer.build(archive, loaders=self.loaders, boards=['j71b'])
        result = self.run_core(archive, 'adopt')
        self.assertEqual(result.returncode, 0, result.stderr)
        launched = subprocess.run([str(self.home / '.local/bin/orchard'), 'version'], capture_output=True,
                                  text=True, env={**os.environ, 'HOME': str(self.home)})
        self.assertEqual(launched.stdout.strip(), receipt['version'], launched.stderr)

    def test_update_installs_a_newer_release_only(self):
        archive = self.home / 'orchard-core.pyz'
        installer.build(archive, loaders=self.loaders, boards=['j71b'])
        data = archive.read_bytes()
        sums = f'{hashlib.sha256(data).hexdigest()}  orchard-core.pyz\n'.encode()

        def opener(url):
            return io.BytesIO(sums if url.endswith('SHA256SUMS') else data)
        self.core_dir = self.core('99.0.0')
        installer.adopt(self.core_dir, '/py', output=lambda _: None)
        messages = []
        self.assertFalse(installer.update(output=messages.append, opener=opener))
        self.assertIn('up to date', messages[0])
        with self.assertRaisesRegex(ValueError, 'checksum'):
            installer.update(opener=lambda url: io.BytesIO(sums if url.endswith('SHA256SUMS') else b'other'))


class StateVersion(unittest.TestCase):
    def test_a_newer_layout_is_refused_and_an_older_one_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(check_state(root), 0)
            self.assertEqual(check_state(root), STATE_VERSION)
            (root / 'state.json').write_text(json.dumps({'version': STATE_VERSION + 1}))
            with self.assertRaisesRegex(ValueError, 'newer orchard'):
                check_state(root)


if __name__ == '__main__':
    unittest.main()

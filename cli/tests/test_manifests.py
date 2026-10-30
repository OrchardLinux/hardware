"""Offline index -> one installation manifest -> verified files. No devices."""
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1]
from orchard_hardware import device_path

DISTRO = CLI / 'tests/fixtures/first'
MINIMAL = CLI / 'tests/fixtures/second'
sys.path.insert(0, str(CLI / 'tools'))
from validate_manifest import ManifestError, load, resolve_local, validate, verify_file, verify_local_receipts


class ManifestContracts(unittest.TestCase):
    def setUp(self):
        self.manifest = load(DISTRO / 'j71b-checkm8-pongo.toml')
        self.index = load(DISTRO / 'index.toml')

    def rejects(self, document):
        with self.assertRaises(ManifestError):
            validate(document)

    def test_all_fixtures_validate_with_local_receipts(self):
        paths = list(DISTRO.glob('*.toml')) + list(MINIMAL.glob('*.toml'))
        paths += list((CLI / 'tests/fixtures/developer').glob('*.toml'))
        # An installed package may be reached through a virtualenv lib64 link.
        paths += [device_path('j71b').resolve()]
        for path in paths:
            with self.subTest(path=path):
                verify_local_receipts(load(path), path.parent)

    def test_device_method_resolves_one_manifest(self):
        path, manifest = resolve_local(self.index, DISTRO, 'j71b', 'checkm8-pongo')
        self.assertEqual(path, DISTRO / 'j71b-checkm8-pongo.toml')
        self.assertEqual(manifest, self.manifest)
        self.assertNotIn('variants', manifest)
        self.assertNotIn('devices', manifest)
        self.assertNotIn('artifacts', manifest)

    def test_two_distro_repos_use_the_same_resolution_path(self):
        resolved = []
        for directory, distro in ((DISTRO, 'fixture-linux'), (MINIMAL, 'other-fixture-linux')):
            index = load(directory / 'index.toml')
            path, manifest = resolve_local(index, directory, 'j71b', 'checkm8-pongo')
            self.assertEqual(manifest['distro'], distro)
            verify_local_receipts(manifest, path.parent)
            resolved.append(manifest)
        self.assertEqual(resolved[0]['device'], resolved[1]['device'])
        self.assertEqual(resolved[0]['boot_method'], resolved[1]['boot_method'])
        self.assertNotEqual(resolved[0]['install']['default_root'], resolved[1]['install']['default_root'])

    def test_no_matching_target_is_an_error(self):
        for device, method in (('missing', 'checkm8-pongo'), ('j71b', 'missing')):
            with self.subTest(device=device, method=method):
                with self.assertRaises(ManifestError):
                    resolve_local(self.index, DISTRO, device, method)

    def test_index_scope_must_match_resolved_manifest(self):
        for field in ('distro', 'device', 'boot_method'):
            with self.subTest(field=field):
                index = deepcopy(self.index)
                if field == 'distro':
                    index['distro'] = 'another-distro'
                    device, method = 'j71b', 'checkm8-pongo'
                elif field == 'device':
                    index['devices']['other'] = index['devices'].pop('j71b')
                    device, method = 'other', 'checkm8-pongo'
                else:
                    methods = index['devices']['j71b']
                    methods['other'] = methods.pop('checkm8-pongo')
                    device, method = 'j71b', 'other'
                with self.assertRaises(ManifestError):
                    resolve_local(index, DISTRO, device, method)

    def test_manifest_pin_checked_before_loading(self):
        self.index['devices']['j71b']['checkm8-pongo']['sha256'] = '0' * 64
        with self.assertRaises(ManifestError):
            resolve_local(self.index, DISTRO, 'j71b', 'checkm8-pongo')

    def test_second_device_can_share_root_and_boot_without_changing_first(self):
        text = (DISTRO / 'j71b-checkm8-pongo.toml').read_text()
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory).resolve()
            (bundle / 'first.toml').write_text(text)
            second_text = text.replace('device = "j71b"', 'device = "synthetic-second-device"')
            (bundle / 'second.toml').write_text(second_text)
            index = deepcopy(self.index)
            for device, filename in (('j71b', 'first.toml'), ('synthetic-second-device', 'second.toml')):
                data = (bundle / filename).read_bytes()
                index['devices'][device] = {'checkm8-pongo': {
                    'file': filename, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}
            _, first = resolve_local(index, bundle, 'j71b', 'checkm8-pongo')
            _, second = resolve_local(index, bundle, 'synthetic-second-device', 'checkm8-pongo')
            self.assertEqual(first['root'], second['root'])
            self.assertEqual(first['boot'], second['boot'])
            self.assertEqual((bundle / 'first.toml').read_text(), text)

    def test_no_workflow_or_hook_registry_or_other_devices(self):
        for key in ('variants', 'devices', 'artifacts', 'completion', 'finalize', 'hardware_abi', 'handoff'):
            with self.subTest(key=key):
                document = deepcopy(self.manifest)
                document[key] = {}
                self.rejects(document)

    def test_unknown_fields_and_schema_versions_rejected(self):
        for key, value in (('schema', 2), ('kind', 'unknown'), ('kind', {}), ('commands', [])):
            with self.subTest(key=key):
                document = deepcopy(self.manifest)
                document[key] = value
                self.rejects(document)

    def test_receipts_cannot_be_missing_or_ambiguous(self):
        for key, value in (('bytes', 0), ('bytes', True), ('sha256', 'placeholder')):
            with self.subTest(key=key):
                document = deepcopy(self.manifest)
                document['root'][key] = value
                self.rejects(document)
        self.manifest['root']['url'] = 'https://downloads.example.invalid/root.tar.gz'
        self.rejects(self.manifest)
        del self.manifest['root']['file']
        validate(self.manifest)
        del self.manifest['root']['url']
        self.rejects(self.manifest)

    def test_exact_https_urls_for_artifacts_and_index_references(self):
        for source in (self.manifest['root'], self.index['devices']['j71b']['checkm8-pongo']):
            source.pop('file')
            source['url'] = 'https://downloads.example.invalid/content?version=1'
        validate(self.manifest)
        validate(self.index)

    def test_unsafe_sources_rejected(self):
        for field, values in (
            ('file', ['/etc/passwd', '../root', 'a/../root', './root', 'a//root', 'C:/root', 'a\\b', 'a\x00']),
            ('url', ['http://example.invalid/root', 'file:///etc/passwd',
                     'https://user:password@example.invalid/root', 'https://example.invalid/root#fragment',
                     'https:///missing', 'https://example.invalid:70000/root',
                     'https://example.invalid/white space', 'https://example.invalid\\other/root'])):
            for value in values:
                with self.subTest(field=field, value=value):
                    document = deepcopy(self.manifest)
                    document['root'].pop('file')
                    document['root'][field] = value
                    self.rejects(document)

    def test_local_checks_never_download(self):
        source = self.manifest['root']
        source.pop('file')
        source['url'] = 'https://downloads.example.invalid/root.tar.gz'
        with self.assertRaises(ManifestError):
            verify_local_receipts(self.manifest, DISTRO)

    def test_corrupt_missing_and_linked_assets_rejected(self):
        data = b'verification fixture'
        receipt = {'file': 'asset', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory).resolve()
            asset = bundle / 'asset'
            with self.assertRaises(ManifestError):
                verify_file(receipt, bundle)
            asset.write_bytes(data)
            verify_file(receipt, bundle)
            asset.write_bytes(b'x' * len(data))
            with self.assertRaises(ManifestError):
                verify_file(receipt, bundle)
            asset.unlink()
            target = bundle / 'actual'
            target.write_bytes(data)
            asset.symlink_to(target)
            with self.assertRaises(ManifestError):
                verify_file(receipt, bundle)

    def test_root_receipt_limits_match_fixture_archive(self):
        with tarfile.open(DISTRO / self.manifest['root']['file']) as archive:
            members = archive.getmembers()
        self.assertEqual(len(members), self.manifest['root']['members'])
        self.assertEqual(sum(member.size for member in members), self.manifest['root']['expanded_bytes'])

    def test_root_selection_supports_filesystem_root_and_current_directory_layout(self):
        for path in ('/', '/omarchy', '/debian'):
            self.manifest['install']['default_root'] = path
            validate(self.manifest)
        for path in ('omarchy', '/..', '/a/../b', '//root'):
            self.manifest['install']['default_root'] = path
            self.rejects(self.manifest)

    def test_developer_boot_requires_no_distro_or_root(self):
        document = load(CLI / 'tests/fixtures/developer/develop.toml')
        self.assertNotIn('distro', document)
        self.assertNotIn('root', document)
        verify_local_receipts(document, CLI / 'tests/fixtures/developer')

    def test_other_boot_methods_do_not_universally_require_pongo(self):
        self.manifest['boot_method'] = 'synthetic-other-method'
        del self.manifest['pongo']
        validate(self.manifest) # Execution still requires an installed method implementation.
        self.manifest['boot_method'] = 'checkm8-pongo'
        self.rejects(self.manifest)

    def test_no_systemd_or_desktop_setting_in_headless_manifest(self):
        document = load(MINIMAL / 'j71b-checkm8-pongo.toml')
        self.assertNotIn('image', document)
        self.assertNotIn('completion', document)
        self.assertNotIn('init', document)

    def test_hardware_descriptor_remains_independent(self):
        document = load(device_path('j71b'))
        self.assertEqual(document['match']['dfu'], {'chip': 0x8010, 'board': 0x18})
        document['requires'] = {'orchard': '0.1.0'}
        self.rejects(document)

    def test_hardware_firmware_recipe_path_is_contained(self):
        device = load(device_path('j71b'))
        for value in ('../firmware.json', '/tmp/firmware.json', 'a//firmware.json', 'a\\\\b'):
            with self.subTest(value=value):
                document = deepcopy(device)
                document['firmware']['recipe'] = value
                self.rejects(document)
        del device['firmware']
        self.rejects(device)

    def test_hardware_firmware_recipe_must_exist_and_match_device(self):
        device = load(device_path('j71b'))
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory).resolve()
            with self.assertRaises(ManifestError):
                verify_local_receipts(device, bundle)
            recipe = bundle / 'firmware.json'
            recipe.write_text('{"device": "another-device"}')
            with self.assertRaises(ManifestError):
                verify_local_receipts(device, bundle)
            recipe.write_text('{"device": "iPad7,5"}')
            verify_local_receipts(device, bundle)
            recipe.unlink()
            target = bundle / 'actual.json'
            target.write_text('{"device": "iPad7,5"}')
            recipe.symlink_to(target)
            with self.assertRaises(ManifestError):
                verify_local_receipts(device, bundle)

    def test_minimum_cli_version_not_exact_pin(self):
        self.assertEqual(self.manifest['min_orchard'], '0.1.0')
        self.manifest['orchard_exact'] = '0.1.0'
        self.rejects(self.manifest)

    def test_cli_failure_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.toml'
            path.write_text('schema = 999\nkind = "orchard.install"\n')
            process = subprocess.run([sys.executable, str(CLI / 'tools/validate_manifest.py'), str(path)],
                                     capture_output=True, text=True)
            self.assertEqual(process.returncode, 1)
            self.assertIn('FAIL:', process.stderr)


if __name__ == '__main__':
    unittest.main()

"""Public P105 identity and recipes contain source metadata, not private payloads."""
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import unittest
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p105_data_api_test', ROOT / '__init__.py')
hardware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardware)


class P105Data(unittest.TestCase):
    def setUp(self):
        self.device = hardware.device('p105')
        self.recipe_path = hardware.firmware_path('p105')
        self.recipe = json.loads(self.recipe_path.read_text())

    def test_identity_is_the_wifi_mini_and_has_no_published_launcher_claim(self):
        self.assertEqual(self.device['schema'], 1)
        self.assertEqual(self.device['kind'], 'hardware.device')
        self.assertEqual(self.device['device']['profile'], 'p105')
        self.assertEqual(self.device['device']['apple_products'], ['iPad2,5'])
        self.assertEqual(self.device['device']['dt_compatible'], ['apple,p105ap'])
        self.assertEqual(self.device['match']['dfu'], {'chip': 0x8942, 'board': 0x0a})
        self.assertEqual(self.device['boot']['methods'], ['jailbreak-kdfu'])
        self.assertNotIn('launch_manifest', self.device['boot'])
        self.assertNotIn('linux', self.device['match'])
        self.assertNotIn('distro', self.device)
        self.assertEqual(self.recipe['device'], 'iPad2,5')
        self.assertEqual(self.recipe['preparation']['mode'], 'manual-source-only')
        self.assertIs(self.recipe['preparation']['orchard_adapter'], False)

    def test_public_recipe_and_destinations_are_local_pinned_and_contained(self):
        self.assertEqual(self.recipe_path, hardware.device_path('p105').parent / 'firmware.json')
        self.assertFalse(self.recipe_path.is_symlink())
        source = urlsplit(self.recipe['ipsw_url'])
        self.assertEqual(source.scheme, 'https')
        self.assertEqual(source.hostname, 'secure-appldnld.apple.com')
        self.assertEqual((self.recipe['version'], self.recipe['build']), ('8.4.1', '12H321'))
        self.assertEqual(self.recipe['ipsw_bytes'], 1509764204)
        self.assertEqual(self.recipe['ipsw_sha256'],
                         'ce6cd59a4a3271eccb93b31c8d40e94ad966112999576663b3ab235196154756')
        self.assertRegex(self.recipe['preparation']['source_commit'], r'^[0-9a-f]{40}$')
        entries = self.recipe['files'] + self.recipe['generated']
        self.assertEqual(len({entry['destination'] for entry in entries}), len(entries))
        for entry in entries:
            destination = PurePosixPath(entry['destination'])
            self.assertFalse(destination.is_absolute())
            self.assertNotIn('..', destination.parts)
            self.assertEqual(destination.parts[:2], ('lib', 'firmware'))
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
        for pattern in self.recipe['extract_patterns']:
            re.compile(pattern)

    def test_own_device_calibration_is_required_and_has_no_public_hash_or_bytes(self):
        private = self.recipe['private_inputs']
        self.assertEqual(len(private), 1)
        calibration = private[0]
        self.assertEqual(calibration['name'], 'mtcal.bin')
        self.assertEqual(calibration['destination'], 'lib/firmware/mtcal.bin')
        self.assertIs(calibration['required'], True)
        self.assertIs(calibration['allow_reference_fallback'], False)
        self.assertIs(calibration['publish'], False)
        self.assertFalse(set(calibration) & {'sha256', 'sha1', 'md5', 'bytes', 'size', 'data', 'base64'})
        public = self.recipe['files'] + self.recipe['generated'] + self.recipe['extracted_inputs']
        self.assertNotIn('mtcal.bin', {entry['name'] for entry in public})
        self.assertNotIn('lib/firmware/mtcal.bin', {entry.get('destination') for entry in public})

    def test_public_p105_resources_are_text_without_firmware_keys_or_identity_records(self):
        resources = []
        for relative in ('devices/p105', 'runtime/p105', 'kernel/p105'):
            directory = ROOT / relative
            self.assertTrue(directory.is_dir(), relative)
            for path in directory.rglob('*'):
                if '__pycache__' in path.parts or path.name == '.DS_Store':
                    continue
                self.assertFalse(path.is_symlink(), str(path))
                if path.is_file():
                    resources.append(path)
        self.assertIn(ROOT / 'runtime/p105/install.py', resources)
        self.assertIn(ROOT / 'kernel/p105/prepare-source', resources)
        prohibited_suffixes = {'.bin', '.fw', '.hcd', '.mtprops', '.z2fw', '.ipsw', '.dmg',
                               '.dfu', '.dtb', '.ko', '.pem', '.key', '.pub', '.pyc'}
        private_names = {'mtcal.bin', 'authorized_keys', 'known_hosts', 'identity.json',
                         'id_rsa', 'id_ed25519'}
        private_fields = {'ecid', 'udid', 'serial_number', 'serialnumber', 'mac_address', 'wifi_mac'}

        def check_fields(value):
            if isinstance(value, dict):
                self.assertFalse({key.lower() for key in value} & private_fields)
                for child in value.values():
                    check_fields(child)
            elif isinstance(value, list):
                for child in value:
                    check_fields(child)

        for path in resources:
            with self.subTest(resource=path.relative_to(ROOT).as_posix()):
                self.assertNotIn(path.suffix.lower(), prohibited_suffixes)
                self.assertNotIn(path.name.lower(), private_names)
                self.assertFalse(path.name.startswith('ssh_host_'))
                data = path.read_bytes()
                self.assertFalse(data.startswith((b'\x7fELF', b'\x1f\x8b', b'\xfd7zXZ\x00', b'\xcf\xfa\xed\xfe')))
                text = data.decode('utf-8')
                self.assertNotRegex(text, r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----')
                self.assertNotIn('/Users/', text)
                if path.suffix == '.json':
                    check_fields(json.loads(text))

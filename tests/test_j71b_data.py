"""Device identities, firmware recipes and pinned upstream inputs."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
try:
    import tomllib
except ImportError:
    import tomli as tomllib
import unittest
from urllib.parse import urlsplit

HARDWARE = Path(__file__).resolve().parents[1]
DEVICE = HARDWARE / 'devices/j71b/device.toml'
RECIPE_SHA256 = '6ce6f51f9a7816bc62c5fa8313758d1e9fa320e1e21ace8f690762cb0893a25d'


class J71bData(unittest.TestCase):
    def setUp(self):
        self.device = tomllib.loads(DEVICE.read_text())
        relative = self.device['firmware']['recipe']
        self.recipe_path = DEVICE.parent / relative
        self.recipe = json.loads(self.recipe_path.read_text())

    def test_recipe_reference_is_device_local(self):
        relative = self.device['firmware']['recipe']
        self.assertFalse(PurePosixPath(relative).is_absolute())
        self.assertNotIn('\\', relative)
        self.assertTrue(all(part not in ('', '.', '..') for part in relative.split('/')))
        self.assertFalse(self.recipe_path.is_symlink())

    def test_upstream_source_pins(self):
        for relative, project, commit in (
                ('boot/m1n1/upstream.lock', 'm1n1', 'd5a10ac52a6468484854419a6c5130f1d62073eb'),
                ('kernel/upstream.lock', 'linux', '6831bc701a6ce059e71e5aaa9488c9195bea6927')):
            lock = dict(line.split('=', 1) for line in (HARDWARE / relative).read_text().splitlines() if '=' in line)
            self.assertEqual(lock['url'], 'https://github.com/OrchardLinux/' + project + '.git')
            self.assertEqual(lock['commit'], commit)

    def test_firmware_recipe_integrity(self):
        self.assertEqual(hashlib.sha256(self.recipe_path.read_bytes()).hexdigest(), RECIPE_SHA256)

    def test_device_identity_matches_recipe(self):
        self.assertEqual(self.device['device']['profile'], 'j71b')
        self.assertEqual(self.device['device']['apple_products'], [self.recipe['device']])
        self.assertEqual(self.device['device']['dt_compatible'], ['apple,j71b'])
        self.assertEqual(self.device['match']['dfu'], {'chip': 0x8010, 'board': 0x18})
        self.assertEqual(self.device['match']['linux']['product'], 'J71b Recovery')

    def test_ipsw_is_pinned(self):
        url = urlsplit(self.recipe['ipsw_url'])
        self.assertEqual(url.scheme, 'https')
        self.assertEqual(url.hostname, 'updates.cdn-apple.com')
        self.assertEqual(self.recipe['version'], '17.7.11')
        self.assertEqual(self.recipe['build'], '21H461')
        self.assertEqual(self.recipe['ipsw_bytes'], 6849577393)
        self.assertRegex(self.recipe['ipsw_sha256'], r'^[0-9a-f]{64}$')

    def test_extraction_patterns_and_input_hashes_retained(self):
        self.assertEqual(self.recipe['extract_patterns'], ['C-4355__s-C1/', 'J71b[.]mtprops$'])
        self.assertEqual(self.recipe['extracted_inputs'][0]['name'], 'J71b.mtprops')
        for entry in self.recipe['files'] + self.recipe['extracted_inputs']:
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
        for pattern in self.recipe['extract_patterns']:
            re.compile(pattern)

    def test_data_only_destinations_unique_and_contained(self):
        entries = self.recipe['files'] + self.recipe['generated']
        self.assertEqual(len(entries), 10)
        self.assertEqual(len({entry['name'] for entry in entries}), len(entries))
        self.assertEqual(len({entry['destination'] for entry in entries}), len(entries))
        for entry in entries:
            path = PurePosixPath(entry['destination'])
            self.assertFalse(path.is_absolute())
            self.assertNotIn('..', path.parts)
            self.assertEqual(path.parts[:2], ('lib', 'firmware'))
        self.assertEqual(self.recipe['generated'], [{
            'name': 'j71b-fwstart.z2fw',
            'destination': 'lib/firmware/apple/j71b-fwstart.z2fw',
            'generator': 'j71b-touch',
        }])

    def test_no_orchard_or_distro_dependency_in_device_definition(self):
        self.assertNotIn('requires', self.device)
        self.assertNotIn('distro', self.device)
        self.assertNotIn('completion', self.device)
        self.assertEqual(self.device['boot']['methods'], ['checkm8-pongo'])



if __name__ == '__main__':
    unittest.main()

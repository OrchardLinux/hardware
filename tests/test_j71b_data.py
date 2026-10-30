"""Device identities, firmware recipes and pinned upstream inputs."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import tomllib
import unittest
from urllib.parse import urlsplit

HARDWARE = Path(__file__).resolve().parents[1]
DEVICE = HARDWARE / 'devices/j71b/device.toml'
RECIPE_SHA256 = '84989247671c3c1c9704da7112e30dd80c76c8a06d0cbb58824d0adace315d9c'


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
        for relative, url, commit in (
                # Asahi m1n1 at Hoolock's merge base; Hoolock and Orchard are patch layers.
                ('boot/m1n1/upstream.lock', 'https://github.com/AsahiLinux/m1n1.git',
                 'c9ef56f7356e93e6f6cac116c529d31c277ef6a6'),
                # torvalds v7.3-rc1; Hoolock and contributors are patch layers.
                ('kernel/upstream.lock', 'https://github.com/torvalds/linux.git',
                 'cee9395acd8043be0644b25c34bfa86623f2b935')):
            lock = dict(line.split('=', 1) for line in (HARDWARE / relative).read_text().splitlines() if '=' in line)
            self.assertEqual(lock['url'], url)
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
        ipsw = self.recipe['ipsw']
        url = urlsplit(ipsw['url'])
        self.assertEqual(url.scheme, 'https')
        self.assertEqual(url.hostname, 'updates.cdn-apple.com')
        self.assertEqual(ipsw['version'], '17.7.11')
        self.assertEqual(ipsw['build'], '21H461')
        self.assertEqual(ipsw['bytes'], 6849577393)
        self.assertRegex(ipsw['sha256'], r'^[0-9a-f]{64}$')

    def test_recipe_matches_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest('jsonschema is not a hardware dependency; the CLI validates recipes')
        schema = json.loads((HARDWARE / 'schemas/v1/firmware.schema.json').read_text())
        jsonschema.validate(self.recipe, schema, cls=jsonschema.Draft202012Validator)

    def test_every_output_is_pinned_and_uses_known_inputs(self):
        outputs = self.recipe['outputs']
        self.assertEqual(len(outputs), 11)
        self.assertEqual(len({o['destination'] for o in outputs}), len(outputs))
        for output in outputs:
            self.assertRegex(output['sha256'], r'^[0-9a-f]{64}$')
            for step in output['steps']:
                self.assertIn(step['input'], self.recipe['inputs'])
        touch = [o for o in outputs if o['destination'] == 'lib/firmware/apple/j71b-fwstart.z2fw']
        self.assertEqual(touch[0]['steps'][0]['step'], 'z2fw-from-mtprops@1')

    def test_aop_uses_the_cached_ipsw_without_embedded_firmware(self):
        aop = self.recipe['inputs']['aop.im4p']
        self.assertEqual(aop['ipsw'], {'zip': 'Firmware/AOP/aopfw-ipad7baop.RELEASE.im4p'})
        self.assertEqual(aop['sha256'], '58fb27cfcd42cab4f3154fb47d92cc7d51e4329caad9b2432ec5130a2423607e')
        output, = [o for o in self.recipe['outputs'] if o['destination'].endswith('/j71b-aop.osbin')]
        self.assertEqual(output['steps'], [{'step': 'im4p-payload@1', 'input': 'aop.im4p'}])
        self.assertFalse((HARDWARE / output['destination']).exists())

    def test_no_orchard_or_distro_dependency_in_device_definition(self):
        self.assertNotIn('requires', self.device)
        self.assertNotIn('distro', self.device)
        self.assertNotIn('completion', self.device)
        self.assertEqual(self.device['boot']['methods'], ['checkm8-pongo'])



if __name__ == '__main__':
    unittest.main()

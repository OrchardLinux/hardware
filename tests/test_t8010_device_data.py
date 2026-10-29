"""Static contracts for the T8010 boards described next to J71b (data only, no downloads)."""
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
SHA256 = re.compile(r'^[0-9a-f]{64}$')

# profile: (Apple product, BDID from the IPSW BuildManifest ApBoardID, IPSW build)
BOARDS = {
    'j72b': ('iPad7,6', 0x1a, '21H461'),
    'j171': ('iPad7,11', 0x1c, '22H384'),
    'j172': ('iPad7,12', 0x1e, '22H384'),
    'd10': ('iPhone9,1', 0x08, '19H422'),
    'd101': ('iPhone9,3', 0x0c, '19H422'),
    'd11': ('iPhone9,2', 0x0a, '19H422'),
    'd111': ('iPhone9,4', 0x0e, '19H422'),
}
# Recipe steps implemented by the Orchard CLI (docs/firmware-recipes.md).
STEPS = {'copy@1', 'bluetool-hcd@1', 'smartio-from-kernelcache@1', 'im4p-payload@1', 'z2fw-from-mtprops@1',
         'z2fw-from-mtprops@2', 'z2fw-from-mtfw@1'}
# Touch firmware the device tree requests (firmware-name); J171 shares the iPad 7 DT with J172.
TOUCH = {'d11': 'dfrmtfw-d11-c1f5e-2.bin', 'd111': 'dfrmtfw-d111-c1f5e-2.bin',
         'j171': 'dfrmtfw-j172-k1f19-6.bin', 'j172': 'dfrmtfw-j172-k1f19-6.bin'}


class T8010DeviceData(unittest.TestCase):
    def load(self, profile):
        device = tomllib.loads((HARDWARE / 'devices' / profile / 'device.toml').read_text())
        recipe = json.loads((HARDWARE / 'devices' / profile / device['firmware']['recipe']).read_text())
        return device, recipe

    def test_identity(self):
        for profile, (product, board, _) in BOARDS.items():
            with self.subTest(profile=profile):
                device, recipe = self.load(profile)
                self.assertEqual(device['device']['profile'], profile)
                self.assertEqual(device['device']['apple_products'], [product])
                self.assertEqual(device['device']['dt_compatible'], ['apple,' + profile])
                self.assertEqual(device['match']['dfu'], {'chip': 0x8010, 'board': board})
                self.assertEqual(device['match']['linux']['product'],
                                 profile[0].upper() + profile[1:] + ' Recovery')
                self.assertEqual(device['kernel'], {'platform': 'apple', 'dtb': 't8010-' + profile})
                self.assertIs(device['support']['install'], True)
                self.assertEqual(recipe['device'], product)

    def test_ipsw_pinned(self):
        for profile, (_, _, build) in BOARDS.items():
            with self.subTest(profile=profile):
                ipsw = self.load(profile)[1]['ipsw']
                self.assertEqual(ipsw['build'], build)
                self.assertEqual(urlsplit(ipsw['url']).hostname, 'updates.cdn-apple.com')
                self.assertIsInstance(ipsw['bytes'], int)
                self.assertRegex(ipsw['sha256'], SHA256)

    def test_outputs_pinned_unique_contained_and_known_steps(self):
        for profile in BOARDS:
            with self.subTest(profile=profile):
                recipe = self.load(profile)[1]
                destinations = [o['destination'] for o in recipe['outputs']]
                self.assertEqual(len(destinations), len(set(destinations)))
                for output in recipe['outputs']:
                    self.assertRegex(output['sha256'], SHA256)
                    self.assertEqual(PurePosixPath(output['destination']).parts[:2], ('lib', 'firmware'))
                    self.assertIn(output['steps'][0]['input'], recipe['inputs'])
                    for step in output['steps']:
                        self.assertIn(step['step'], STEPS)

    def test_bluetooth_vendor_images_are_installed_side_by_side(self):
        # Which image a unit needs depends on its module vendor; neither is guessed.
        for profile in BOARDS:
            with self.subTest(profile=profile):
                names = {PurePosixPath(o['destination']).name for o in self.load(profile)[1]['outputs']
                         if o['steps'][0]['step'] == 'bluetool-hcd@1'}
                self.assertEqual(names, {f'BCM.apple,{profile}-MUR.hcd', f'BCM.apple,{profile}-USI.hcd'})

    def test_kernel_request_names(self):
        for profile in BOARDS:
            with self.subTest(profile=profile):
                names = [PurePosixPath(o['destination']).name for o in self.load(profile)[1]['outputs']]
                for name in names:
                    self.assertTrue(name.startswith(('brcmfmac4355', 'BCM.apple,', 't8010-smartio.bin',
                                                     profile + '-fwstart')) or name == TOUCH.get(profile), name)

    def test_touch_is_untested_exactly_where_firmware_is_built(self):
        for profile in BOARDS:
            with self.subTest(profile=profile):
                device, recipe = self.load(profile)
                names = {PurePosixPath(o['destination']).name for o in recipe['outputs']}
                if profile in TOUCH:
                    self.assertIn(TOUCH[profile], names)
                touch = bool(names & {TOUCH.get(profile), profile + '-fwstart.z2fw'})
                self.assertEqual(device['features']['touch'], 'untested' if touch else 'missing')


if __name__ == '__main__':
    unittest.main()

"""N61 identity, private touch firmware and experimental descriptor contracts."""
import importlib.util
import json
from pathlib import Path
import re
import unittest

HARDWARE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('n61_hardware_data', HARDWARE / 'src/orchard/hardware.py')
data = importlib.util.module_from_spec(spec)
spec.loader.exec_module(data)


class N61Data(unittest.TestCase):
    def setUp(self):
        self.device = data.device('n61')

    def test_board_identity_and_platform(self):
        self.assertIn('n61', data.profiles())
        self.assertEqual(self.device['device']['apple_products'], ['iPhone7,2'])
        self.assertEqual(self.device['device']['dt_compatible'], ['apple,n61'])
        self.assertEqual(self.device['match']['dfu'], {'chip': 0x7000, 'board': 0x06})
        self.assertEqual(self.device['kernel'], {'platform': 'apple-4k', 'dtb': 't7000-n61'})
        self.assertEqual(self.device['boot'], {'methods': ['checkm8-pongo']})

    def test_observed_minimal_boot_and_touch_keep_installation_experimental(self):
        self.assertIs(self.device['support']['install'], False)
        self.assertEqual(self.device['support']['status'], 'experimental')
        self.assertIs(self.device['support']['usb_serial'], True)
        self.assertIs(self.device['support']['usb_network'], False)
        self.assertIs(self.device['support']['wifi'], False)
        expected = dict.fromkeys(data.features(), 'missing')
        expected.update(display='working', touch='working', battery='untested', backlight='untested', buttons='untested')
        self.assertEqual(self.device['features'], expected)
        caveats = ' '.join(self.device['support']['caveats'])
        self.assertIn('minimal-boot RAM image', caveats)
        self.assertIn('PongoOS 2.6.3-e98323f8', caveats)
        self.assertIn('evdev contacts and releases', caveats)
        self.assertIn('earlier desktop results are recorded as history', caveats)
        self.assertIn('Bluetooth, Wi-Fi and internal storage are unqualified', caveats)

    def test_touch_recipe_selects_the_pinned_n61_gen1_profile(self):
        path = data.firmware_path('n61')
        self.assertEqual(path, HARDWARE / 'devices/n61/firmware.json')
        recipe = json.loads(path.read_text())
        self.assertEqual((recipe['schema'], recipe['device']), (2, 'iPhone7,2'))
        output, = recipe['outputs']
        self.assertEqual(output['destination'], 'lib/firmware/apple/n61-fwstart.z2fw')
        self.assertEqual(output['sha256'], '51d2ef7b25c1d7e323fa703b531223312cb76f7183fbeb425f921dd6e5509294')
        self.assertEqual(output['steps'], [{'step': 'z2fw-from-mtprops@3', 'input': 'N61.mtprops',
            'profile': 'C1F59,1', 'packet': 'Z2', 'touch_maximum': 1024, 'prox_maximum': 128,
            'fll_mval': 6099, 'ref_clk_div': 2, 'clk32_clock_enable': 1}])
        self.assertFalse(self.device['support']['install'])



class DescriptorSchema(unittest.TestCase):
    def setUp(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest('jsonschema is not a hardware dependency; the CLI validates descriptors')
        schema = json.loads((HARDWARE / 'schemas/v1/device.schema.json').read_text())
        Draft202012Validator.check_schema(schema)
        self.validator = Draft202012Validator(schema)

    def test_all_packaged_descriptors_validate(self):
        for profile in data.profiles():
            with self.subTest(profile=profile):
                self.validator.validate(data.device(profile))

    def test_firmware_is_required_for_every_descriptor(self):
        for profile in data.profiles():
            with self.subTest(profile=profile):
                candidate = data.device(profile)
                del candidate['firmware']
                self.assertFalse(self.validator.is_valid(candidate))

    def test_firmware_declaration_still_requires_a_recipe(self):
        candidate = data.device('n61')
        candidate['firmware'] = {}
        self.assertFalse(self.validator.is_valid(candidate))
        candidate['firmware'] = {'recipe': ''}
        self.assertFalse(self.validator.is_valid(candidate))
        candidate['firmware'] = None
        self.assertFalse(self.validator.is_valid(candidate))
        candidate = data.device('j71b')
        del candidate['firmware']
        self.assertFalse(self.validator.is_valid(candidate))


class Apple4kKernelConfig(unittest.TestCase):
    @staticmethod
    def config(platform):
        settings = {}
        for line in (HARDWARE / 'kernel/configs' / platform / 'config').read_text().splitlines():
            if line.startswith('CONFIG_'):
                name, value = line.split('=', 1)
                settings[name] = value
            else:
                disabled = re.fullmatch(r'# (CONFIG_\w+) is not set', line)
                if disabled:
                    settings[disabled[1]] = 'n'
        return settings

    def test_a8_granule_and_existing_platform_stay_distinct(self):
        for platform, granule, shift in (('apple-4k', '4K', '12'), ('apple-16k', '16K', '14')):
            with self.subTest(platform=platform):
                settings = self.config(platform)
                self.assertEqual(settings['CONFIG_ARM64_' + granule + '_PAGES'], 'y')
                self.assertEqual(settings['CONFIG_PAGE_SHIFT'], shift)
                for other in {'4K', '16K', '64K'} - {granule}:
                    self.assertEqual(settings['CONFIG_ARM64_' + other + '_PAGES'], 'n')

    def test_ram_console_drivers_are_built_in(self):
        settings = self.config('apple-4k')
        for name in ('CONFIG_APPLE_USBCOMPLEX', 'CONFIG_PHY_APPLE_AUSB', 'CONFIG_USB_DWC2',
                     'CONFIG_USB_GADGET', 'CONFIG_USB_CONFIGFS', 'CONFIG_USB_CONFIGFS_ACM',
                     'CONFIG_DRM_SIMPLEDRM'):
            with self.subTest(option=name):
                self.assertEqual(settings[name], 'y')
        self.assertEqual(settings['CONFIG_MTD_RAW_NAND'], 'n')
        self.assertEqual(settings['CONFIG_USB_CONFIGFS_MASS_STORAGE'], 'n')
        self.assertEqual(settings['CONFIG_USB_G_MULTI'], 'm')
        self.assertEqual(self.config('apple-16k')['CONFIG_USB_G_MULTI'], 'm')

    def test_both_platforms_reuse_the_same_ordered_patch_layers(self):
        spec = importlib.util.spec_from_file_location('n61_kernel_series', HARDWARE / 'kernel/series.py')
        series = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(series)
        self.assertEqual(series.resolve('n61'), ('apple-4k', 't7000-n61'))
        self.assertEqual(series.patches('apple-4k'), series.patches('apple-16k'))
        self.assertTrue(series.patches('apple-4k'))


if __name__ == '__main__':
    unittest.main()

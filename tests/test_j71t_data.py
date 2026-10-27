"""J71t identity, firmware recipe and device-specific recovery/runtime files."""
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
DEVICE = HARDWARE / 'devices/j71t/device.toml'
RECIPE_SHA256 = 'a04519b0e93cd040e2df11e3d99e732ed8c2559203ee67ae57a27fa548f3aeb5'
WINDOW = 'nvme.write_window=1:0-4294967295'


class J71tData(unittest.TestCase):
    def setUp(self):
        self.device = tomllib.loads(DEVICE.read_text())
        self.recipe_path = DEVICE.parent / self.device['firmware']['recipe']
        self.recipe = json.loads(self.recipe_path.read_text())

    def test_firmware_recipe_integrity(self):
        self.assertFalse(self.recipe_path.is_symlink())
        self.assertEqual(hashlib.sha256(self.recipe_path.read_bytes()).hexdigest(), RECIPE_SHA256)

    def test_device_identity_matches_recipe(self):
        self.assertEqual(self.device['device']['profile'], 'j71t')
        self.assertEqual(self.device['device']['apple_products'], [self.recipe['device']])
        self.assertEqual(self.device['device']['dt_compatible'], ['apple,j71t'])
        # iPad6,11 is also J71s (S8000, chip 0x8000); only S8003 matches.
        self.assertEqual(self.device['match']['dfu'], {'chip': 0x8003, 'board': 0x10})
        self.assertEqual(self.device['match']['linux']['product'], 'J71t Recovery')
        self.assertEqual(self.device['boot']['methods'], ['checkm8-pongo'])
        self.assertNotIn('launch_manifest', self.device['boot'])  # No J71t launcher is published yet.

    def test_ipsw_is_pinned(self):
        url = urlsplit(self.recipe['ipsw_url'])
        self.assertEqual(url.scheme, 'https')
        self.assertEqual(url.hostname, 'updates.cdn-apple.com')
        self.assertEqual((self.recipe['version'], self.recipe['build']), ('16.7.10', '20H350'))
        self.assertEqual(self.recipe['ipsw_bytes'], 5586431671)
        self.assertRegex(self.recipe['ipsw_sha256'], r'^[0-9a-f]{64}$')

    def test_extraction_patterns_and_input_hashes(self):
        self.assertEqual(self.recipe['extract_patterns'], ['C-4355__s-C0/', 'J71t[.]mtprops$'])
        self.assertEqual([entry['name'] for entry in self.recipe['extracted_inputs']], ['J71t.mtprops'])
        for entry in self.recipe['files'] + self.recipe['extracted_inputs']:
            self.assertRegex(entry['sha256'], r'^[0-9a-f]{64}$')
        for pattern in self.recipe['extract_patterns']:
            re.compile(pattern)

    def test_wifi_names_follow_brcmfmac_board_fallbacks(self):
        # brcmfmac tries apple,feara-MODULE-VENDOR-VERSION before apple,feara,
        # so every NVRAM variant is installed and one firmware image serves all.
        brcm = {entry['name']: PurePosixPath(entry['destination']).name for entry in self.recipe['files']
                if entry['destination'].startswith('lib/firmware/brcm/brcmfmac')}
        self.assertEqual(brcm.pop('feara.trx'), 'brcmfmac4355-pcie.apple,feara.bin')
        self.assertEqual(brcm.pop('feara.clmb'), 'brcmfmac4355-pcie.apple,feara.clm_blob')
        self.assertEqual(brcm.pop('feara.txcb'), 'brcmfmac4355-pcie.apple,feara.txcap_blob')
        self.assertEqual(len(brcm), 9)
        for name, destination in brcm.items():
            module, vendor, version = re.fullmatch(r'P-feara_M-(\w+)_V-(\w)__m-([0-9.]+)\.txt', name).groups()
            self.assertEqual(destination, f'brcmfmac4355-pcie.apple,feara-{module}-{vendor}-{version}.txt')

    def test_data_only_destinations_unique_and_contained(self):
        entries = self.recipe['files'] + self.recipe['generated']
        self.assertEqual(len(entries), 19)
        self.assertEqual(len({entry['name'] for entry in entries}), len(entries))
        self.assertEqual(len({entry['destination'] for entry in entries}), len(entries))
        for entry in entries:
            path = PurePosixPath(entry['destination'])
            self.assertFalse(path.is_absolute())
            self.assertNotIn('..', path.parts)
            self.assertEqual(path.parts[:2], ('lib', 'firmware'))
        self.assertEqual({entry['generator'] for entry in self.recipe['generated']}, {'j71t-adt'})
        self.assertEqual([entry['name'] for entry in self.recipe['generated']],
                         ['j71t-n1-calibration.bin', 'j71t-LSCI.bin', 'j71t-CBAT.bin', 'j71t-BMac.bin'])

    def test_recovery_identity_matches_device(self):
        recovery = HARDWARE / 'boot/initramfs/j71t/recovery'
        self.assertIn('= apple,j71t ]', (recovery / 'init').read_text())
        self.assertIn('/usr/libexec/orchard-j71t/handoff.py', (recovery / 'init').read_text())
        gadget = (recovery / 'usb-gadget').read_text()
        self.assertIn("echo '" + self.device['match']['linux']['product'] + "'", gadget)
        self.assertIn('echo 0x4142 > "$g/idProduct"', gadget)
        for name in ('boot.json', 'boot-verbose.json'):
            bootargs = json.loads((recovery / name).read_text())['bootargs']
            self.assertTrue(bootargs.startswith('chosen.bootargs=j71t.recovery=1 '))
            self.assertEqual(bootargs.split().count(WINDOW), 1)
            self.assertLess(len(bootargs), 1000)

    def test_hardware_preparation_keeps_one_storage_window(self):
        runtime = HARDWARE / 'runtime/j71t'
        window = WINDOW.split('=', 1)[1]
        phase2 = (runtime / 'phase2-hardware').read_text()
        self.assertIn('case "$window" in 1:[0-9]*-[0-9]*) ;; *) false ;; esac', phase2)
        self.assertIn('blockdev --setro', phase2)
        self.assertNotIn('--setrw', phase2)
        self.assertNotIn('mount ', phase2.replace('/proc/mounts', ''))
        self.assertIn('write_window)" in 1:[0-9]*-[0-9]*)', (runtime / 'j71t-check-platform').read_text())
        device = (HARDWARE / 'runtime/j71b/device.py').read_text()
        pattern = re.search(r"'j71t': \('/sys/module/nvme/parameters/write_window', '([^']+)'\)", device).group(1)
        self.assertTrue(re.fullmatch(pattern, window))
        self.assertTrue(re.fullmatch(pattern, '1:3146240-7812095'))
        for refused in ('', '2:0-4294967295', '1:0-', 'N'):
            self.assertFalse(re.fullmatch(pattern, refused))


if __name__ == '__main__':
    unittest.main()

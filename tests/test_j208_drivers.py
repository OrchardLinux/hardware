"""J208 keeps its RAM-verified drivers without changing the shared kernel series."""
import importlib.util
import json
from pathlib import Path
import unittest

HARDWARE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class J208Drivers(unittest.TestCase):
    def setUp(self):
        self.data = load("j208_hardware_data", HARDWARE / "src/orchard/hardware.py")
        self.device = self.data.device("j208")

    def test_descriptor_stays_experimental_and_unique(self):
        self.assertIn("j208", self.data.profiles())
        self.assertEqual(self.device["device"]["apple_products"], ["iPad7,4"])
        self.assertEqual(self.device["device"]["dt_compatible"], ["apple,j208"])
        self.assertEqual(self.device["match"]["dfu"], {"chip": 0x8011, "board": 0x06})
        self.assertEqual(self.device["kernel"], {"platform": "apple-16k", "dtb": "t8011-j208"})
        self.assertIs(self.device["support"]["install"], False)
        self.assertEqual(self.device["support"]["status"], "experimental")
        self.assertNotIn("linux", self.device["match"])
        identities = []
        for profile in self.data.profiles():
            dfu = self.data.device(profile)["match"]["dfu"]
            identities.append((dfu["chip"], dfu["board"]))
        self.assertEqual(len(identities), len(set(identities)))

    def test_sources_are_outside_the_shared_series(self):
        drivers = HARDWARE / "devices/j208/drivers"
        patches = sorted(path.name for path in (drivers / "patches").glob("*.patch"))
        self.assertEqual(patches, [
            "0001-t8011-sio-firmware-gate.patch",
            "0002-spi-native-word-size-shift.patch",
            "0003-j208-native-pio-timing.patch",
            "0004-j208-pcie-wifi-port.patch",
            "0005-j208-wlan-provider.patch",
            "0006-j208-brcmfmac-profile.patch",
            "0007-j208-touch-reports.patch",
        ])
        modules = [path for path in (drivers / "modules").rglob("*") if path.is_file()]
        self.assertTrue(modules)
        for path in modules:
            self.assertNotIn(path.suffix, {".ko", ".o", ".mod"})
        text = "\n".join(path.read_text(errors="replace") for path in modules if path.suffix in {".c", ".h", ".dts"})
        self.assertIn("apple,j208", text)
        config = (HARDWARE / "devices/j208/ram-kernel.config").read_text()
        self.assertIn("# CONFIG_BLK_DEV_NVME is not set\n", config)
        series = load("j208_kernel_series", HARDWARE / "kernel/series.py")
        applied = [str(path.relative_to(HARDWARE / "kernel")) for path in series.patches("apple-16k")]
        self.assertTrue(applied)
        self.assertFalse(any("j208" in path for path in applied))
        self.assertFalse((HARDWARE / "kernel/patches/j208").exists())

    def test_descriptor_matches_the_device_schema(self):
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((HARDWARE / "schemas/v1/device.schema.json").read_text())
        Draft202012Validator(schema).validate(self.device)
        self.assertEqual(set(self.device["features"]), set(self.data.features()))


if __name__ == "__main__":
    unittest.main()


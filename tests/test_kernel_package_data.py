"""Kernel producers must have their complete layered inputs after wheel install."""
import glob
import os
from pathlib import Path
import subprocess
import sys
import unittest
import zipfile

import tomllib


ROOT = Path(__file__).resolve().parents[1]


def required_kernel_resources():
    required = {"kernel/series.py"}
    for series in (ROOT / "kernel/series").iterdir():
        if not series.is_file():
            continue
        required.add(str(series.relative_to(ROOT)))
        output = subprocess.check_output(
            [sys.executable, str(ROOT / "kernel/series.py"), series.name], text=True
        )
        required.update(str(Path(line).relative_to(ROOT)) for line in output.splitlines() if line)
    return required


class KernelPackageData(unittest.TestCase):
    def test_declared_package_data_covers_every_platform_series(self):
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        patterns = config["tool"]["setuptools"]["package-data"]["orchard_hardware"]
        included = {
            str(Path(name).relative_to(ROOT))
            for pattern in patterns
            for name in glob.glob(str(ROOT / pattern), recursive=True)
            if Path(name).is_file()
        }
        missing = required_kernel_resources() - included
        self.assertFalse(missing, "Kernel resources missing from package data: " + ", ".join(sorted(missing)))

    @unittest.skipUnless(os.environ.get("ORCHARD_HARDWARE_WHEEL"), "Set ORCHARD_HARDWARE_WHEEL to validate a built wheel")
    def test_built_wheel_contains_every_platform_series(self):
        with zipfile.ZipFile(os.environ["ORCHARD_HARDWARE_WHEEL"]) as wheel:
            included = set(wheel.namelist())
        missing = {"orchard_hardware/" + path for path in required_kernel_resources()} - included
        self.assertFalse(missing, "Built wheel is missing kernel resources: " + ", ".join(sorted(missing)))


if __name__ == "__main__":
    unittest.main()

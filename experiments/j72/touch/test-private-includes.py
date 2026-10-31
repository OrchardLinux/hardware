#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Synthetic inputs only: rendering, validation and private file modes."""
import importlib.util
from pathlib import Path
import re
import stat
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('packets', Path(__file__).with_name('make-private-includes.py'))
packets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packets)


class PrivateIncludes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.inputs = {}
        for key, (name, size) in packets.PACKETS.items():
            path = self.root / key
            path.write_bytes(bytes(i % 256 for i in range(size)))
            self.inputs[key] = path
        self.output = self.root / 'private'

    def test_round_trip_and_private_permissions(self):
        names = packets.render(self.inputs, self.output)
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o700)
        for name in names:
            path = self.output / name
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            actual = bytes(int(v, 16) for v in re.findall(r'0x([0-9a-f]{2})', path.read_text()))
            key = next(k for k, (n, _) in packets.PACKETS.items() if n == name)
            self.assertEqual(actual, self.inputs[key].read_bytes())

    def test_all_wrong_sizes_refused_before_outputs(self):
        for key, (_, size) in packets.PACKETS.items():
            with self.subTest(key=key):
                self.inputs[key].write_bytes(b'x' * (size - 1))
                with self.assertRaises(ValueError):
                    packets.render(self.inputs, self.output)
                self.assertFalse(self.output.exists())
                self.inputs[key].write_bytes(b'x' * size)

    def test_existing_files_are_not_overwritten(self):
        packets.render(self.inputs, self.output)
        original = (self.output / 'j72_fw_packet.inc').read_bytes()
        with self.assertRaises(FileExistsError):
            packets.render(self.inputs, self.output)
        self.assertEqual((self.output / 'j72_fw_packet.inc').read_bytes(), original)

    def test_output_symlink_refused(self):
        target = self.root / 'target'
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            packets.render(self.inputs, self.output)
        self.assertEqual(list(target.iterdir()), [])


if __name__ == '__main__':
    unittest.main()

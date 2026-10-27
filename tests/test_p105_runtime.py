"""P105 installation guards using synthetic payloads and temporary guest roots."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('p105_installer_test', ROOT / 'runtime/p105/install.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
CALIBRATION = b'synthetic own-device calibration; never an Apple reference blob\n'


def arm_elf(*, dynamic=False, home=True):
    ident = b'\x7fELF\x01\x01\x01' + bytes(9)
    header = struct.pack('<16sHHIIIIIHHHHHH', ident, 2, 40, 1, 0, 52, 0,
                         0x05000400, 52, 32, 1, 40, 0, 0)
    program = struct.pack('<IIIIIIII', 3 if dynamic else 1, 0, 0, 0, 84, 84, 5, 4)
    return header + program + (b'--desktop-home\0Cascadia P105 Home\0' if home else b'')


def tree(path):
    return {entry.relative_to(path).as_posix():
            ('link', os.readlink(entry)) if entry.is_symlink() else ('file', entry.read_bytes())
            for entry in path.rglob('*') if entry.is_symlink() or entry.is_file()}


class P105Runtime(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def fixture(self, name='case'):
        parent = self.base / name
        root, source = parent / 'guest', parent / 'initramfs'
        for relative, data in (('usr/lib/systemd/systemd', arm_elf(dynamic=True)),
                               ('usr/bin/sh', b'#!/bin/sh\n')):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        for line in (installer.OVERLAY / 'usr/lib/cascadia/p105-payload.list').read_text().splitlines():
            relative, _, _ = line.split('|')
            target = source / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == 'lib/firmware/mtcal.bin':
                data = CALIBRATION
            elif relative in ('sbin/z2-boot', 'sbin/p105-keys', 'bin/busybox.static'):
                data = arm_elf()
            else:
                data = b'synthetic firmware fixture\n'
            target.write_bytes(data)
        calibration = parent / 'own-calibration.bin'
        calibration.write_bytes(CALIBRATION)
        return SimpleNamespace(root=root, initramfs=source, calibration=calibration,
                               allow_user_owned_staging=True)

    def install(self, args):
        # The tests never need chown, privilege escalation or a hardware process.
        with patch.object(installer.os, 'geteuid', return_value=1000), contextlib.redirect_stdout(io.StringIO()):
            installer.install(args)

    def test_host_root_and_alias_to_host_root_are_refused(self):
        args = self.fixture()
        alias = self.base / 'host-root'
        alias.symlink_to('/')
        for root in (Path('/'), alias):
            with self.subTest(root=root), patch.object(installer, 'place', side_effect=AssertionError('must not copy')):
                args.root = root
                with self.assertRaisesRegex(ValueError, 'guest root'):
                    self.install(args)

    def test_non_root_requires_explicit_staging_opt_in(self):
        args = self.fixture()
        args.allow_user_owned_staging = False
        before = tree(args.root)
        with self.assertRaisesRegex(ValueError, 'allow-user-owned-staging'):
            self.install(args)
        self.assertEqual(tree(args.root), before)

    def test_guest_needs_an_arm32_systemd(self):
        for label, data in (('missing', None), ('text', b'not ELF'),
                            ('ELF64', arm_elf()[:4] + b'\x02' + arm_elf()[5:])):
            with self.subTest(format=label):
                args = self.fixture(label)
                binary = args.root / 'usr/lib/systemd/systemd'
                if data is None:
                    binary.unlink()
                else:
                    binary.write_bytes(data)
                before = tree(args.root)
                with self.assertRaises((ValueError, OSError)):
                    self.install(args)
                self.assertEqual(tree(args.root), before)

    def test_missing_empty_dynamic_and_old_hardware_payloads_stop_before_writes(self):
        cases = (
            ('missing', 'lib/firmware/brcm/brcmfmac4334.bin', None),
            ('empty', 'lib/firmware/P105.mtprops', b''),
            ('non-arm', 'sbin/z2-boot', b'not ARM'),
            ('dynamic', 'bin/busybox.static', arm_elf(dynamic=True)),
            ('old-buttons', 'sbin/p105-keys', arm_elf(home=False)),
        )
        for label, relative, data in cases:
            with self.subTest(payload=label):
                args = self.fixture(label)
                source = args.initramfs / relative
                if data is None:
                    source.unlink()
                else:
                    source.write_bytes(data)
                before = tree(args.root)
                with self.assertRaises((ValueError, OSError)):
                    self.install(args)
                self.assertEqual(tree(args.root), before)

    def test_calibration_requires_a_separate_regular_own_file_matching_this_payload(self):
        for label in ('missing', 'empty', 'different', 'embedded', 'inside-tree', 'linked'):
            with self.subTest(calibration=label):
                args = self.fixture(label)
                if label == 'missing':
                    args.calibration.unlink()
                elif label == 'empty':
                    args.calibration.write_bytes(b'')
                elif label == 'different':
                    args.calibration.write_bytes(b'other synthetic device')
                elif label == 'embedded':
                    args.calibration = args.initramfs / 'lib/firmware/mtcal.bin'
                elif label == 'inside-tree':
                    args.calibration = args.initramfs / 'own-copy.bin'
                    args.calibration.write_bytes(CALIBRATION)
                elif label == 'linked':
                    link = args.calibration.with_name('linked-calibration')
                    link.symlink_to(args.calibration)
                    args.calibration = link
                before = tree(args.root)
                with self.assertRaises((ValueError, OSError)):
                    self.install(args)
                self.assertEqual(tree(args.root), before)

    def test_source_and_destination_symlink_escapes_leave_outside_files_untouched(self):
        for kind in ('source', 'destination'):
            with self.subTest(link=kind):
                args = self.fixture(kind)
                outside = self.base / (kind + '-outside')
                outside.mkdir()
                sentinel = outside / 'sentinel'
                sentinel.write_bytes(b'unchanged')
                if kind == 'source':
                    source = args.initramfs / 'lib/firmware/brcm/brcmfmac4334.bin'
                    source.unlink()
                    source.symlink_to(sentinel)
                else:
                    (args.root / 'usr/libexec').symlink_to(outside, target_is_directory=True)
                before = tree(outside)
                with self.assertRaises((ValueError, OSError)):
                    self.install(args)
                self.assertEqual(tree(outside), before)

    def test_preexisting_temporary_symlink_cannot_redirect_a_guest_write(self):
        args = self.fixture()
        outside = self.base / 'outside'
        outside.write_bytes(b'unchanged')
        trap = args.root / 'etc/cascadia/systemd-root.p105-new'
        trap.parent.mkdir(parents=True)
        trap.symlink_to(outside)
        self.install(args)
        self.assertEqual(outside.read_bytes(), b'unchanged')
        self.assertEqual((args.root / 'etc/cascadia/systemd-root').read_text(), installer.MARKER)

    def test_incompatible_existing_root_marker_is_refused(self):
        args = self.fixture()
        marker = args.root / 'etc/cascadia/systemd-root'
        marker.parent.mkdir(parents=True)
        marker.write_text('some-other-root\n')
        before = tree(args.root)
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.install(args)
        self.assertEqual(tree(args.root), before)

    def test_contained_staging_records_exact_payload_and_preserves_modes(self):
        args = self.fixture()
        self.install(args)
        record = json.loads((args.root / 'usr/lib/cascadia/p105-runtime.json').read_text())
        self.assertEqual(record['device'], 'iPad2,5 / p105ap')
        self.assertFalse(record['guest_root_ownership_applied'])
        self.assertEqual((args.root / 'usr/lib/firmware/mtcal.bin').read_bytes(), CALIBRATION)
        for line in (installer.OVERLAY / 'usr/lib/cascadia/p105-payload.list').read_text().splitlines():
            source, destination, mode = line.split('|')
            target = args.root / destination
            self.assertEqual(target.read_bytes(), (args.initramfs / source).read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, int(mode, 8))
            self.assertEqual(record['payload'][destination], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual((args.root / 'tmp').stat().st_mode & 0o7777, 0o1777)
        for relative, destination in installer.ENABLE.items():
            self.assertEqual(os.readlink(args.root / 'etc/systemd/system' / relative), destination)

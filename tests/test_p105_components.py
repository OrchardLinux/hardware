"""Component boundaries using synthetic inputs and disposable files."""
import argparse
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import struct
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('p105_components_test', str(ROOT / 'kernel/p105/build'))
spec = importlib.util.spec_from_loader(loader.name, loader)
producer = importlib.util.module_from_spec(spec)
loader.exec_module(producer)


class P105Components(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)

    def outputs(self, private=False):
        output = self.base / 'components'
        output.mkdir()
        kernel = bytes(36) + bytes.fromhex('18286f01') + bytes(8)
        dtb = bytes.fromhex('d00dfeed') + struct.pack('>I', 40) + bytes(32)
        config = '# CONFIG_MTD is not set\nCONFIG_INITRAMFS_SOURCE=""\n'
        for name, data in {'zImage': kernel, 'p105ap.dtb': dtb, 'kernel.config': config.encode(),
                           'kernel.release': b'6.12.0\n', 'compiler.txt': b'fixture compiler\n',
                           'binutils.txt': b'fixture binutils\n'}.items():
            (output / name).write_bytes(data)
        if private:
            loader_bytes = b'synthetic loader'
            (output / 'staging-loader.bin').write_bytes(loader_bytes)
            (output / 'staging-bundle.bin').write_bytes(loader_bytes.ljust(32768, b'\0') + kernel + dtb)
            (output / 'initramfs.tar').write_bytes(b'synthetic private archive')
        return output

    def args(self, **changes):
        values = dict(work=self.base / 'work', output=self.base / 'output',
                      private_initramfs=None, calibration=None, jobs=2,
                      nfs_root=None,
                      cascadia_source=None, linux_bundle=None,
                      toolchain_image='unused', keep_work=False)
        values.update(changes)
        return argparse.Namespace(**values)

    def test_public_output_refuses_private_payloads_and_unexpected_files(self):
        output = self.outputs()
        producer.validate_outputs(output, False)
        for name in ('initramfs.tar', 'mtcal.bin', 'staging-bundle.bin'):
            with self.subTest(name=name):
                (output / name).write_bytes(b'synthetic private input')
                with self.assertRaisesRegex(ValueError, 'output set'):
                    producer.validate_outputs(output, False)
                (output / name).unlink()

    def test_public_kernel_requires_empty_initramfs_and_disabled_nand(self):
        output = self.outputs()
        for config in ('CONFIG_INITRAMFS_SOURCE="private-root"\n',
                       'CONFIG_INITRAMFS_SOURCE=""\nCONFIG_MTD_APPLE_H2FMI=y\n',
                       'CONFIG_INITRAMFS_SOURCE=""\nCONFIG_MTD_APPLE_H2FMI=m\n'):
            with self.subTest(config=config):
                (output / 'kernel.config').write_text(config)
                with self.assertRaises(ValueError):
                    producer.validate_outputs(output, False)

    def test_private_bundle_must_match_exact_loader_kernel_and_dtb(self):
        output = self.outputs(private=True)
        producer.validate_outputs(output, True)
        bundle = output / 'staging-bundle.bin'
        content = bundle.read_bytes()
        for data in (content[:-1], content[:32768] + b'changed', b'wrong loader' + content[12:]):
            bundle.write_bytes(data)
            with self.assertRaisesRegex(ValueError, 'bundle differs'):
                producer.validate_outputs(output, True)

    def test_linked_component_is_refused(self):
        output = self.outputs()
        component = output / 'compiler.txt'
        component.unlink()
        component.symlink_to('/etc/passwd')
        with self.assertRaisesRegex(ValueError, 'output set'):
            producer.validate_outputs(output, False)

    def test_private_input_pair_is_required_before_any_external_operation(self):
        for args in (self.args(private_initramfs=self.base / 'root'),
                     self.args(calibration=self.base / 'owner.bin')):
            with self.subTest(args=args), patch.object(producer, 'run', side_effect=AssertionError('external operation')):
                with self.assertRaisesRegex(ValueError, 'supplied together'):
                    producer.build(args)
                self.assertFalse(args.work.exists())

    def test_nfs_setting_is_private_and_rejects_multiline_or_relative_inputs(self):
        self.assertEqual(producer.nfs_location('10.55.0.1:/export/desktop'), '10.55.0.1:/export/desktop')
        for value in ('host:relative', 'host:/root\nother:/root', 'host:/root\x00', '/root', 'host:/'):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'nfs-root'):
                producer.nfs_location(value)
        args = self.args(nfs_root='10.55.0.1:/export/desktop')
        with patch.object(producer, 'run', side_effect=AssertionError('external operation')):
            with self.assertRaisesRegex(ValueError, 'private boot root'):
                producer.build(args)
        self.assertFalse(args.work.exists())

    def test_wrong_calibration_is_refused_before_work_creation(self):
        root = self.base / 'root'
        (root / 'lib/firmware').mkdir(parents=True)
        (root / 'lib/firmware/mtcal.bin').write_bytes(b'panel A synthetic')
        calibration = self.base / 'owner.bin'
        calibration.write_bytes(b'panel B synthetic')
        args = self.args(private_initramfs=root, calibration=calibration)
        with patch.object(producer, 'run', side_effect=AssertionError('external operation')):
            with self.assertRaisesRegex(ValueError, 'calibration differs'):
                producer.build(args)
        self.assertFalse(args.work.exists())

    def test_work_and_output_cannot_alias_the_hardware_package(self):
        alias = self.base / 'package'
        alias.symlink_to(ROOT, target_is_directory=True)
        for destination in (ROOT / 'private-new-build', alias / 'private-new-build'):
            with self.subTest(path=destination), self.assertRaisesRegex(ValueError, 'outside'):
                producer.fresh(destination)

    def test_overlay_cannot_follow_guest_links_into_host_files(self):
        source, guest, outside = (self.base / name for name in ('source', 'guest', 'outside'))
        for directory in (source / 'etc', guest, outside):
            directory.mkdir(parents=True)
        (source / 'etc/test').write_bytes(b'overlay')
        (outside / 'test').write_bytes(b'preserve outside')
        (guest / 'etc').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Linked overlay destination'):
            producer.overlay(source, guest)
        self.assertEqual((outside / 'test').read_bytes(), b'preserve outside')

    def test_archive_preserves_links_without_reading_targets_and_maps_ownership(self):
        source = self.base / 'source'
        source.mkdir()
        (source / 'program').write_bytes(b'fixture binary')
        (source / 'host-link').symlink_to('/outside/unavailable/private-input')
        archive = self.base / 'input.tar'
        inventory = producer.archive_tree(source, archive)
        self.assertEqual(inventory['host-link'], {'link': '/outside/unavailable/private-input'})
        with tarfile.open(archive) as stream:
            for member in stream:
                self.assertEqual((member.uid, member.gid), (0, 0))
            self.assertTrue(stream.getmember('host-link').issym())

    def test_empty_mount_points_survive_private_boot_archiving(self):
        root = self.base / 'boot-root'
        root.mkdir()
        producer.boot_mounts(root)
        archive = self.base / 'boot.tar'
        inventory = producer.archive_tree(root, archive)
        with tarfile.open(archive) as stream:
            for name in ('dev', 'proc', 'sys', 'run', 'tmp'):
                self.assertTrue(stream.getmember(name).isdir())
                self.assertTrue(inventory[name]['directory'])
        self.assertEqual((root / 'tmp').stat().st_mode & 0o7777, 0o1777)

    def test_early_mount_point_cannot_alias_an_outside_directory(self):
        root = self.base / 'boot-root'
        outside = self.base / 'outside'
        root.mkdir(); outside.mkdir()
        (root / 'dev').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'early mount point'):
            producer.boot_mounts(root)

    def test_packaged_default_disables_the_reference_flash_controller(self):
        preparation = producer.module('p105_component_pins_test', ROOT / 'kernel/p105/prepare-source')
        config = preparation.merge_config('CONFIG_MTD=y\nCONFIG_MTD_APPLE_H2FMI=y\n',
                                          (ROOT / 'kernel/p105/config.fragment').read_text())
        self.assertIn('CONFIG_MTD=y', config.splitlines())
        self.assertIn('# CONFIG_MTD_APPLE_H2FMI is not set', config.splitlines())
        self.assertIn('+\t\t\tstatus = "disabled";', (ROOT / 'kernel/p105/nand-disabled.patch').read_text())


if __name__ == '__main__':
    unittest.main()

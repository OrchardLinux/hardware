"""RAM building/boot tests. Synthetic components; real packer, mocked USB."""
from contextlib import nullcontext
import gzip
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from orchard import hardware as hardware_data
from orchard import boot, build, ram


class RamTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        # The board is the developer's choice, here this checkout's local file.
        (self.base / 'orchard.local.toml').write_text('device = "j71b"\n')
        environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.base / 'config-home')})
        environment.start()
        self.addCleanup(environment.stop)
        self.hardware = hardware_data.ROOT
        self.kernel = self.base / 'kernel'
        modules = self.kernel / 'lib/modules/test-release'
        modules.mkdir(parents=True)
        (modules / 'modules.dep').write_text('test.ko:\n')
        (modules / 'test.ko').write_bytes(b'not a real module')
        (self.kernel / 'kernel.release').write_text('test-release\n')
        (self.kernel / 'Image.gz').write_bytes(b'not a real kernel')
        (self.kernel / 'j71b.dtb').write_bytes(b'not a real DTB')
        # The kernel bundle carries every DTB of its platform; payloads take them all.
        self.boards = ram.boards_by_platform(self.hardware)['apple-16k']
        (self.kernel / 'dtbs').mkdir()
        for dtb in self.boards.values():
            (self.kernel / 'dtbs' / (dtb + '.dtb')).write_bytes(dtb.encode())
        elf = bytearray(64)
        elf[:6] = b'\x7fELF\x02\x01'
        elf[18:20] = b'\xb7\x00'
        struct.pack_into('<Q', elf, 32, 64)
        struct.pack_into('<HH', elf, 54, 56, 0)
        self.busybox = self.base / 'busybox'
        self.busybox.write_bytes(elf)
        self.loader, self.pongo = self.base / 'm1n1.bin', self.base / 'pongo.bin'
        self.loader.write_bytes(b'loader')
        self.pongo.write_bytes(b'pongo')
        self.script = self.base / 'test.sh'
        self.script.write_text('#!/bin/sh\necho test\n')
        self.script.chmod(0o755)
        self.init = self.base / 'init'
        self.init.write_text('#!/bin/busybox sh\necho test startup\n')
        self.init.chmod(0o755)
        self.output = self.base / 'out'
        self.components = dict(kernel=self.kernel, busybox=self.busybox,
                               loader=self.loader, pongo=self.pongo)
        # The hardware's static tools, as build.prepare_tools returns them.
        self.tools = self.base / 'tools'
        self.tools.mkdir()
        shutil.copyfile(self.busybox, self.tools / 'busybox')
        (self.tools / 'wpa_supplicant').write_bytes(b'wpa_supplicant')
        tools = patch.object(build, 'prepare_tools', return_value=self.tools)
        tools.start()
        self.addCleanup(tools.stop)
        # The build-time RAM SSH key lives in Orchard's private state.
        home = patch.dict('os.environ', {'ORCHARD_HOME': str(self.base / 'home')})
        home.start()
        self.addCleanup(home.stop)

    def build(self, **kwargs):
        options = dict(init=self.init, files={'usr/bin/test': self.script}, modules=True,
                       platform='apple-16k', boards=self.boards)
        options.update(kwargs)
        return ram.assemble_ram(self.hardware, self.output, **self.components, **options)

    def test_real_packaging_and_repeat(self):
        self.build()
        receipt = ram.verify_ram(self.output)
        self.assertEqual(receipt['platform'], 'apple-16k')
        self.assertIn('j71b', receipt['boards'])
        archive = gzip.decompress((self.output / 'initramfs.cpio.gz').read_bytes())
        self.assertTrue(archive.startswith(b'070701'))
        self.assertIn(b'usr/bin/test\0', archive)
        self.assertIn(b'lib/modules/test-release/test.ko\0', archive)
        receipt = json.loads((self.output / 'linux.bin.json').read_text())
        self.assertEqual([row['name'] for row in receipt['components']],
                         ['loader', 'bootargs', *['dtb'] * len(self.boards), 'kernel', 'initramfs_header', 'initramfs'])
        self.build()
        ram.verify_ram(self.output)

    def test_custom_init_and_no_modules(self):
        custom = self.base / 'init'
        custom.write_text('#!/bin/busybox sh\necho custom\n')
        self.build(init=custom, modules=False, files={})
        archive = gzip.decompress((self.output / 'initramfs.cpio.gz').read_bytes())
        self.assertIn(custom.read_bytes(), archive)
        self.assertNotIn(b'test.ko\0', archive)
        self.assertNotIn(b'charger-test\0', archive)

    def test_changed_payload_and_source_are_refused(self):
        self.build()
        (self.output / 'linux.bin').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            ram.verify_ram(self.output)
        self.build()
        self.script.write_text('changed input')
        with self.assertRaises(ValueError):
            ram.verify_ram(self.output)

    def test_host_launcher_edit_does_not_invalidate_build(self):
        self.build()
        (self.base / 'run.py').write_text('# host-only change')
        ram.verify_ram(self.output)

    def test_failed_build_invalidates_previous_output(self):
        self.build()
        (self.kernel / 'lib/modules/test-release/modules.dep').unlink()
        with self.assertRaises(ValueError):
            self.build()
        self.assertFalse((self.output / 'build.json').exists())

    def test_dynamic_wrong_arch_and_truncated_busybox_rejected(self):
        original = self.busybox.read_bytes()
        for content in (b'bad', original[:6] + b'\0' * 58):
            self.busybox.write_bytes(content)
            with self.assertRaises(ValueError):
                self.build()
        elf = bytearray(original)
        struct.pack_into('<H', elf, 56, 1)
        elf.extend(struct.pack('<I', 3) + b'\0' * 52)
        self.busybox.write_bytes(elf)
        with self.assertRaises(ValueError):
            self.build()

    def test_file_traversal_and_collision_rejected(self):
        for destination in ('../escape', '/escape', 'init', 'bin/busybox'):
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                self.build(files={destination: self.script})

    def test_legacy_payload_survives_host_refactor(self):
        self.build()
        path = self.output / 'build.json'
        receipt = json.loads(path.read_text())
        receipt.pop('kind')
        receipt.pop('sources')
        receipt['example_sources'] = {'removed-host-script': 'old hash'}
        path.write_text(json.dumps(receipt))
        ram.verify_ram(self.output)

    def test_confirmation(self):
        for answer in ('', 'y', 'Y', 'yes', ' YES '):
            with self.subTest(answer=answer), patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value=answer):
                boot.confirm_boot()
        for answer in ('n', 'no', 'boot', 'unknown'):
            with self.subTest(answer=answer), patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value=answer), self.assertRaises(ValueError):
                boot.confirm_boot()
        with patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', side_effect=EOFError), self.assertRaises(ValueError):
            boot.confirm_boot()
        with patch('sys.stdin.isatty', return_value=False), patch('builtins.input') as prompt:
            with self.assertRaises(ValueError):
                boot.confirm_boot()
            prompt.assert_not_called()

    def test_boot_stops_before_usb_mutation(self):
        self.build()
        for device in ({'mode': 'linux'}, {'mode': 'pongo', 'chip': 0x8010,
                         'board': 0x18, 'ecid': 123, 'location': 'fake'}):
            with patch('orchard.apple.usb.sole_target', return_value=device), \
                    patch('orchard.apple.usb.selected') as selected, \
                    patch('orchard.transports.pongo.upload') as upload, \
                    patch('orchard.state.operation_lock', return_value=nullcontext()), \
                    patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value='no'), self.assertRaises(ValueError):
                boot.boot_ram(self.output)
            selected.assert_not_called()
            upload.assert_not_called()

    def test_pongo_boot_uses_no_rpc_or_readiness_claim(self):
        self.build()
        device = dict(mode='pongo', chip=0x8010, board=0x18, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected') as selected, \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value=''), patch('builtins.print') as output:
            boot.boot_ram(self.output)
        self.assertEqual(selected.call_count, 2)
        upload.assert_called_once()
        self.assertIn('NOT been verified', str(output.call_args_list))

    def test_cli_dispatches_directly_without_wrapper_or_outer_device_lock(self):
        from orchard.command import main
        with patch('sys.argv', ['orchard', 'build', str(self.base), '--pongo', 'local.bin']), \
                patch.object(ram, 'build_directory') as build, \
                patch('orchard.command.operation_lock') as lock:
            main()
            build.assert_called_once_with(self.base, hardware=None, cache=None, kernel=None,
                                          busybox=None, loader=None, pongo=Path('local.bin'), device=None,
                                          platform=None)
            lock.assert_not_called()
        with patch('sys.argv', ['orchard', 'run', str(self.base)]), \
                patch.object(boot, 'run_directory') as run:
            main()
            run.assert_called_once_with(self.base, yes=False, build=True, device=None, platform=None,
                                        hardware=None, cache=None, kernel=None, busybox=None, loader=None,
                                        pongo=None)

    def test_cli_noninteractive_run_flags(self):
        from orchard.command import main
        for flag in ('--yes', '-y'):
            with self.subTest(flag=flag), \
                    patch('sys.argv', ['orchard', 'run', str(self.base), flag]), \
                    patch.object(boot, 'run_directory') as run, \
                    patch('orchard.command.operation_lock') as lock:
                main()
                self.assertIs(run.call_args.kwargs['yes'], True)
                lock.assert_not_called()

    def test_noninteractive_pongo_boot_skips_only_confirmation(self):
        self.build()
        device = dict(mode='pongo', chip=0x8010, board=0x18, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected') as selected, \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()) as lock, \
                patch.object(boot, 'verify_ram', wraps=ram.verify_ram) as verify, \
                patch('sys.stdin.isatty', return_value=False), \
                patch('builtins.input', side_effect=AssertionError('unexpected prompt')):
            boot.boot_ram(self.output, yes=True)
        lock.assert_called_once()
        self.assertEqual(verify.call_count, 2)
        self.assertEqual(selected.call_count, 2)
        upload.assert_called_once()

    def test_noninteractive_without_yes_refuses_before_usb_mutation(self):
        self.build()
        device = dict(mode='pongo', chip=0x8010, board=0x18, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected') as selected, \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                patch('sys.stdin.isatty', return_value=False), \
                self.assertRaisesRegex(ValueError, '--yes'):
            boot.boot_ram(self.output)
        selected.assert_not_called()
        upload.assert_not_called()

    def test_yes_does_not_bypass_device_checks(self):
        self.build()
        for changes in ({'mode': 'linux'}, {'ecid': None}):
            device = dict(mode='pongo', chip=0x8010, board=0x18, ecid=123, location='fake')
            device.update(changes)
            with self.subTest(changes=changes), \
                    patch('orchard.apple.usb.sole_target', return_value=device), \
                    patch('orchard.apple.usb.selected') as selected, \
                    patch('orchard.transports.pongo.upload') as upload, \
                    patch('orchard.state.operation_lock', return_value=nullcontext()), \
                    self.assertRaises(ValueError):
                boot.boot_ram(self.output, yes=True)
            selected.assert_not_called()
            upload.assert_not_called()

    def test_yes_does_not_bypass_payload_verification(self):
        self.build()
        (self.output / 'linux.bin').write_bytes(b'corrupted')
        with patch('orchard.apple.usb.sole_target') as discover, \
                patch('orchard.transports.pongo.upload') as upload, \
                self.assertRaises(ValueError):
            boot.boot_ram(self.output, yes=True)
        discover.assert_not_called()
        upload.assert_not_called()

    def test_noninteractive_run_never_retries(self):
        built = self.base / 'out/apple-16k'
        built.mkdir(parents=True)
        (built / 'build.json').write_text('{}')
        with patch.object(boot, 'boot_ram', side_effect=RuntimeError('uncertain upload')) as upload, \
                patch.object(ram, 'build_directory', return_value=built), \
                patch('orchard.devices.attached_board', return_value=None), \
                self.assertRaisesRegex(SystemExit, 'Inspect device state'):
            boot.run_directory(self.base, yes=True)
        upload.assert_called_once_with(built, yes=True)

    def test_mismatch_confirmation_defaults_to_no(self):
        for answer in ('', 'n', 'no'):
            with self.subTest(answer=answer), patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value=answer) as prompt, self.assertRaises(ValueError):
                boot.confirm_boot(mismatch=True)
            prompt.assert_called_once_with('Try booting anyway? [y/N] ')
        for answer in ('y', 'YES'):
            with self.subTest(answer=answer), patch('sys.stdin.isatty', return_value=True), \
                    patch('builtins.input', return_value=answer):
                boot.confirm_boot(mismatch=True)

    def test_mismatched_device_can_boot_after_warning(self):
        self.build()
        device = dict(mode='pongo', chip=0x8011, board=0x04, ecid=123, location='fake')
        for yes in (False, True):
            with self.subTest(yes=yes), \
                    patch('orchard.apple.usb.sole_target', return_value=device), \
                    patch('orchard.apple.usb.selected') as selected, \
                    patch('orchard.transports.pongo.upload') as upload, \
                    patch('orchard.state.operation_lock', return_value=nullcontext()), \
                    patch('sys.stdin.isatty', return_value=not yes), \
                    patch('builtins.input', return_value='yes') as prompt, \
                    patch('builtins.print') as output:
                boot.boot_ram(self.output, yes=yes)
            if yes:
                prompt.assert_not_called()
            else:
                prompt.assert_called_once_with('Try booting anyway? [y/N] ')
            self.assertIn('This build was prepared for apple-16k boards', str(output.call_args_list))
            self.assertIn('your connected device is not one of them', str(output.call_args_list))
            self.assertIn('until you restart it', str(output.call_args_list))
            self.assertEqual(selected.call_count, 2)
            selected.assert_any_call('fake', 123, chip=0x8011, board=0x04, mode='pongo')
            selected.assert_any_call('fake', 123, chip=0x8011, board=0x04)
            upload.assert_called_once()

    def test_unknown_payload_profile_warns_but_can_boot(self):
        self.build()
        path = self.output / 'build.json'
        receipt = json.loads(path.read_text())
        receipt['boards'] = ['experimental-unregistered-board']
        path.write_text(json.dumps(receipt))
        device = dict(mode='pongo', chip=0x8011, board=0x04, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected'), \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                patch('builtins.input', side_effect=AssertionError('unexpected prompt')), \
                patch('builtins.print') as output:
            boot.boot_ram(self.output, yes=True)
        self.assertIn('intended device for this build is unknown', str(output.call_args_list))
        upload.assert_called_once()

    def test_mismatched_dfu_boot_tracks_actual_device_through_pongo(self):
        self.build()
        device = dict(mode='dfu', chip=0x8011, board=0x04, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected') as selected, \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                patch('orchard.apple.boot.wait_mode') as wait, \
                patch.object(boot.shutil, 'which', return_value='/fake/palera1n'), \
                patch('orchard.apple.boot.run_process_group') as execute, \
                patch('builtins.print') as output:
            boot.boot_ram(self.output, yes=True)
        self.assertIn('reconnect the cable', str(output.call_args_list))
        selected.assert_any_call('fake', 123, chip=0x8011, board=0x04, mode='dfu')
        selected.assert_any_call('fake', 123, chip=0x8011, board=0x04)
        wait.assert_called_once_with(device, 'pongo', chip=0x8011, board=0x04, product='', seconds=60)
        execute.assert_called_once()
        upload.assert_called_once()

    def test_mismatch_refusal_stops_before_usb_mutation(self):
        self.build()
        device = dict(mode='pongo', chip=0x8011, board=0x04, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected') as selected, \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                patch('sys.stdin.isatty', return_value=True), \
                patch('builtins.input', return_value=''), self.assertRaises(ValueError):
            boot.boot_ram(self.output)
        selected.assert_not_called()
        upload.assert_not_called()

    def test_yes_still_requires_exactly_one_device(self):
        self.build()
        device = dict(mode='pongo', chip=0x8011, board=0x04, ecid=123, location='fake')
        with patch('orchard.apple.usb.devices', return_value=[device, dict(device, ecid=456)]), \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                self.assertRaisesRegex(ValueError, 'exactly one'):
            boot.boot_ram(self.output, yes=True)
        upload.assert_not_called()

    def test_mismatched_device_identity_change_still_stops_upload(self):
        self.build()
        device = dict(mode='pongo', chip=0x8011, board=0x04, ecid=123, location='fake')
        with patch('orchard.apple.usb.sole_target', return_value=device), \
                patch('orchard.apple.usb.selected', side_effect=ValueError('Wrong device identity')), \
                patch('orchard.transports.pongo.upload') as upload, \
                patch('orchard.state.operation_lock', return_value=nullcontext()), \
                self.assertRaisesRegex(ValueError, 'Wrong device identity'):
            boot.boot_ram(self.output, yes=True)
        upload.assert_not_called()

    def test_a_board_selects_its_platform_and_the_payload_carries_every_dtb(self):
        boards = ram.boards_by_platform(self.hardware)
        for dtb in boards['apple-4k'].values():
            (self.kernel / 'dtbs' / (dtb + '.dtb')).write_bytes(dtb.encode())
        directory = self.base / 'examples/nested/first-boot'
        directory.mkdir(parents=True)
        shutil.copyfile(self.init, directory / 'init')
        for override, platform in ((None, 'apple-16k'), ('n61', 'apple-4k')):  # local file names j71b
            with self.subTest(override=override), \
                    patch.object(build, 'prepare_components', return_value=self.components) as prepare:
                output = ram.build_directory(directory, hardware=self.hardware, device=override)
                self.assertEqual(output, directory / 'out' / platform)
                self.assertEqual(prepare.call_args.kwargs['device'], sorted(boards[platform])[0])
                receipt = ram.verify_ram(output)
                self.assertEqual((receipt['platform'], receipt['boards']), (platform, sorted(boards[platform])))
                packed = json.loads((output / 'linux.bin.json').read_text())
                dtbs = [row['sha256'] for row in packed['components'] if row['name'] == 'dtb']
                self.assertEqual(sorted(dtbs), sorted(build.digest(self.kernel / 'dtbs' / (dtb + '.dtb'))
                                                      for dtb in boards[platform].values()))

    def test_an_undescribed_board_lists_the_platforms(self):
        (self.base / 'orchard.local.toml').write_text('device = "experimental"\n')
        with self.assertRaisesRegex(ValueError, r'No hardware support describes experimental.*apple-16k: .*j71b'):
            ram.build_directory(self.base, hardware=self.hardware)
        (self.base / 'orchard.local.toml').unlink()
        with patch('orchard.devices.attached_board', return_value=None), \
                self.assertRaisesRegex(ValueError, r'Which platform\?.*apple-4k: .*n61'):
            ram.build_directory(self.base, hardware=self.hardware)

    def test_cli_device_option_overrides_project_default(self):
        from orchard.command import main
        with patch('sys.argv', ['orchard', 'build', str(self.base), '--device', 'experimental']), \
                patch.object(ram, 'build_directory') as compose:
            main()
        self.assertEqual(compose.call_args.kwargs['device'], 'experimental')

    def test_invalid_project_config_fails_before_build_or_receipt_changes(self):
        (self.base / 'orchard.local.toml').write_text('device = [')
        self.output.mkdir()
        receipt = self.output / 'build.json'
        receipt.write_text('last successful receipt')
        with patch.object(build, 'prepare_components') as prepare, self.assertRaises(ValueError):
            ram.build_directory(self.base, hardware=self.hardware)
        prepare.assert_not_called()
        self.assertEqual(receipt.read_text(), 'last successful receipt')

    def test_directory_content_builds_without_python_recipes(self):
        example = self.base / 'example'
        example.mkdir()
        shutil.copyfile(self.init, example / 'init')
        tool = example / 'rootfs/usr/bin/test'
        tool.parent.mkdir(parents=True)
        shutil.copy2(self.script, tool)
        with patch.object(build, 'prepare_components', return_value=self.components):
            ram.build_directory(example, hardware=self.hardware)
        output = example / 'out/apple-16k'
        ram.verify_ram(output)
        archive = gzip.decompress((output / 'initramfs.cpio.gz').read_bytes())
        self.assertIn(b'usr/bin/test\0', archive)
        self.assertNotIn(b'test.ko\0', archive)  # modules are opt-in
        self.assertFalse((example / 'build.py').exists())
        (tool.parent / 'new-file').write_text('new input')
        with self.assertRaises(ValueError):
            ram.verify_ram(output)
        (example / 'payload.toml').write_text('modules = true\n')
        with patch.object(build, 'prepare_components', return_value=self.components):
            ram.build_directory(example, hardware=self.hardware)
        self.assertIn(b'test.ko\0', gzip.decompress((output / 'initramfs.cpio.gz').read_bytes()))

    def test_usb_session_is_added_unless_the_project_opts_out(self):
        example = self.base / 'example'
        example.mkdir()
        shutil.copyfile(self.init, example / 'init')
        with patch.object(build, 'prepare_components', return_value=self.components):
            ram.build_directory(example, hardware=self.hardware)
        archive = gzip.decompress((example / 'out/apple-16k/initramfs.cpio.gz').read_bytes())
        self.assertIn(b'usr/libexec/orchard/usb-session\0', archive)
        self.assertIn(b'etc/orchard/authorized_keys\0', archive)
        public = (self.base / 'home/private/ram-ssh/id_ed25519.pub').read_bytes()
        self.assertIn(public, archive)
        self.assertNotIn((self.base / 'home/private/ram-ssh/id_ed25519').read_bytes(), archive)
        (example / 'payload.toml').write_text('transport = false\n')
        with patch.object(build, 'prepare_components', return_value=self.components):
            ram.build_directory(example, hardware=self.hardware)
        archive = gzip.decompress((example / 'out/apple-16k/initramfs.cpio.gz').read_bytes())
        self.assertNotIn(b'usr/libexec/orchard/usb-session\0', archive)
        self.assertNotIn(b'etc/orchard/authorized_keys\0', archive)

    def test_arbitrary_directory_layout_uses_active_hardware_package(self):
        for relative in ('examples/demo', 'use_cases/nested/charger 001', 'unrelated-project'):
            directory = self.base / relative
            directory.mkdir(parents=True)
            shutil.copyfile(self.init, directory / 'init')
            with self.subTest(relative=relative), \
                    patch.object(build, 'prepare_components', return_value=self.components) as prepare, \
                    patch.object(build, 'cache_root', return_value=self.base / 'shared-cache'):
                ram.build_directory(directory)
                # Components are per platform; its first board selects them.
                prepare.assert_called_once_with(self.hardware, self.base / 'shared-cache', device='d10', kernel=None,
                                                busybox=None, loader=None, pongo=None)
                ram.verify_ram(directory / 'out/apple-16k')
                if directory.name == 'charger 001':
                    archive = gzip.decompress((directory / 'out/apple-16k/initramfs.cpio.gz').read_bytes())
                    self.assertIn(b'charger-001\n', archive)

    def test_directory_build_includes_the_hardware_tools(self):
        directory = self.base / 'tools-project'
        directory.mkdir()
        shutil.copyfile(self.init, directory / 'init')
        with patch.object(build, 'prepare_components', return_value=self.components), \
                patch.object(build, 'cache_root', return_value=self.base / 'shared-cache'):
            ram.build_directory(directory)
        archive = gzip.decompress((directory / 'out/apple-16k/initramfs.cpio.gz').read_bytes())
        self.assertIn(b'usr/sbin/wpa_supplicant', archive)
        self.assertNotIn(b'usr/bin/busybox', archive)

    def test_firmware_is_a_layer_for_the_booted_board_outside_the_project(self):
        from orchard import firmware
        directory = self.base / 'firmware-project'
        directory.mkdir()
        shutil.copyfile(self.init, directory / 'init')
        (directory / 'payload.toml').write_text('firmware = true\n')
        outputs = self.base / 'firmware-outputs'
        outputs.mkdir()
        (outputs / ('a' * 64)).write_bytes(b'firmware blob')
        recipe = {'outputs': [{'destination': 'lib/firmware/brcm/test.bin', 'sha256': 'a' * 64}]}
        with patch.object(build, 'prepare_components', return_value=self.components), \
                patch.object(build, 'cache_root', return_value=self.base / 'shared-cache'):
            output = ram.build_directory(directory)
        # The project's payload is the same for every board and holds no firmware.
        self.assertTrue(json.loads((output / 'build.json').read_text())['firmware'])
        self.assertNotIn(b'firmware blob', gzip.decompress((output / 'initramfs.cpio.gz').read_bytes()))
        with patch.object(firmware, 'recipe_for', return_value=recipe) as recipe_for, \
                patch.object(firmware, 'ensure', return_value=outputs) as ensure:
            layered = ram.with_firmware(output, 'j71b', hardware=self.hardware, cache=self.base / 'shared-cache')
            again = ram.with_firmware(output, 'j71b', hardware=self.hardware, cache=self.base / 'shared-cache')
        recipe_for.assert_called_with('j71b', self.hardware)
        self.assertEqual(ensure.call_args.kwargs['hardware'], self.hardware)
        self.assertEqual(layered, again)
        self.assertTrue(layered.is_relative_to(self.base / 'shared-cache/private'))
        receipt = ram.verify_ram(layered)
        self.assertEqual(receipt['boards'], ['j71b'])
        image = (layered / 'linux.bin').read_bytes()
        self.assertIn(gzip.compress(b'', mtime=0)[:3], image)  # gzip members of both layers
        with self.assertRaisesRegex(ValueError, 'not n61'):
            ram.with_firmware(output, 'n61', hardware=self.hardware, cache=self.base / 'shared-cache')

    def test_explicit_hardware_override_takes_precedence_over_active_package(self):
        directory = self.base / 'use_cases/override'
        directory.mkdir(parents=True)
        shutil.copyfile(self.init, directory / 'init')
        with patch.object(hardware_data, 'ROOT', self.base / 'unused-hardware'), \
                patch.object(build, 'prepare_components', return_value=self.components) as prepare:
            ram.build_directory(directory, hardware=self.hardware)
        self.assertEqual(prepare.call_args.args[0], self.hardware)
        ram.verify_ram(directory / 'out/apple-16k')

    def test_run_builds_for_the_attached_board_then_boots(self):
        directory = self.base / 'use_cases/deeply/nested/system'
        directory.mkdir(parents=True)
        shutil.copyfile(self.init, directory / 'init')
        with patch('orchard.devices.attached_board', return_value='n61'), \
                patch.object(ram, 'build_directory', return_value=directory / 'out/apple-4k') as compose, \
                patch.object(boot, 'boot_ram') as upload:
            (directory / 'out/apple-4k').mkdir(parents=True)
            (directory / 'out/apple-4k/build.json').write_text('{"firmware": false}')
            boot.run_directory(directory)
        self.assertEqual(compose.call_args.kwargs['platform'], 'apple-4k')
        upload.assert_called_once_with(directory / 'out/apple-4k', yes=False)

    def test_run_without_build_needs_an_existing_build(self):
        with patch('orchard.devices.attached_board', return_value='j71b'), \
                patch.object(ram, 'build_directory') as compose, \
                self.assertRaisesRegex(SystemExit, 'No apple-16k build'):
            boot.run_directory(self.base, build=False)
        compose.assert_not_called()

    def test_directory_requires_visible_init_before_building_components(self):
        with patch.object(build, 'prepare_components') as components:
            with self.assertRaises(ValueError):
                ram.build_directory(self.base / 'missing-init', hardware=self.hardware)
            components.assert_not_called()

    def test_cache_lock_refuses_nested_use_in_one_build(self):
        cache = self.base / 'cache'
        with build.component_lock(cache):
            with self.assertRaises(ValueError):
                with build.component_lock(cache):
                    self.fail('Lock was not held')
        with build.component_lock(cache):
            pass

    def test_cache_lock_waits_for_another_build(self):
        cache = self.base / 'cache'
        cache.mkdir()
        holder = subprocess.Popen([sys.executable, '-c', (
            'import fcntl, sys, time\n'
            'lock = open(sys.argv[1], "a")\n'
            'fcntl.flock(lock, fcntl.LOCK_EX)\n'
            'print("held", flush=True)\n'
            'time.sleep(1)\n'), str(cache / '.lock')], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), 'held')
        started = time.monotonic()
        with patch('sys.stderr', new=io.StringIO()) as stderr:
            with build.component_lock(cache):
                waited = time.monotonic() - started
        self.assertGreater(waited, 0.5)
        self.assertIn('Waiting for another Orchard build', stderr.getvalue())


if __name__ == '__main__':
    unittest.main()

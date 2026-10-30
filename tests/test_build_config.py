"""Project build defaults, independent of supported-device registration."""
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard import build, service
from orchard import devices
from orchard.apple import device as adapter_module

adapter = devices.load('j71b')


class BuildConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / '.git').mkdir()
        self.example = self.root / 'examples/deep/first-boot'
        self.example.mkdir(parents=True)
        self.local = self.root / 'orchard.local.toml'
        self.home = self.root / 'config-home'
        environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.home)})
        environment.start()
        self.addCleanup(environment.stop)

    def user(self, text):
        (self.home / 'orchard').mkdir(parents=True, exist_ok=True)
        (self.home / 'orchard/config.toml').write_text(text)

    def test_no_choice_selects_no_device(self):
        self.assertIsNone(build.configured_device(self.example))

    def test_checkout_local_choice_applies_to_everything_below_it(self):
        self.local.write_text('device = "unregistered-board"\n')
        self.assertEqual(build.configured_device(self.example), 'unregistered-board')
        self.assertEqual(build.configured_device(self.root), 'unregistered-board')

    def test_flag_then_checkout_then_user_config(self):
        self.user('device = "from-user"\n')
        self.assertEqual(build.resolve_device(self.example), 'from-user')
        self.local.write_text('device = "from-checkout"\n')
        self.assertEqual(build.resolve_device(self.example), 'from-checkout')
        self.assertEqual(build.resolve_device(self.example, 'from-flag'), 'from-flag')

    def test_local_file_naming_only_an_orchard_checkout_leaves_the_device_open(self):
        self.local.write_text('orchard = "../orchard-gpu"\n')
        self.user('device = "from-user"\n')
        self.assertEqual(build.resolve_device(self.example), 'from-user')

    def test_does_not_inherit_another_repositorys_choice(self):
        self.local.write_text('device = "unrelated"\n')
        (self.example / '.git').write_text('gitdir: elsewhere\n')
        self.assertIsNone(build.configured_device(self.example))

    def test_payload_transport_defaults_on_and_can_be_turned_off(self):
        self.assertEqual(build.payload_settings(self.example), {'transport': True})
        (self.example / 'payload.toml').write_text('transport = false\n')
        self.assertEqual(build.payload_settings(self.example), {'transport': False})
        # Only the payload's own directory counts; a parent's file is another payload's.
        self.assertEqual(build.payload_settings(self.example.parent), {'transport': True})

    def test_a_payload_names_no_device(self):
        (self.example / 'payload.toml').write_text('device = "j71b"\n')
        with self.assertRaisesRegex(ValueError, 'unknown settings device'):
            build.payload_settings(self.example)

    def test_invalid_settings_fail_instead_of_silently_defaulting(self):
        for content in ('device = [', 'device = 123', 'device = "../escape"', 'device = ""',
                        'device = "-bad"', 'device = "j71b"\nunused = true'):
            with self.subTest(content=content):
                self.local.write_text(content)
                with self.assertRaisesRegex(ValueError, 'orchard.local.toml'):
                    build.configured_device(self.example)
        (self.example / 'payload.toml').write_text('transport = "no"\n')
        with self.assertRaisesRegex(ValueError, 'payload.toml'):
            build.payload_settings(self.example)

    def test_normal_installer_still_rejects_chip_and_board_mismatches(self):
        for mode in ('dfu', 'pongo'):
            for changes in ({'chip': adapter.CHIP + 1}, {'board': adapter.BOARD + 1}):
                observed = dict(mode=mode, chip=adapter.CHIP, board=adapter.BOARD,
                                ecid=123, location='fake')
                observed.update(changes)
                with self.subTest(mode=mode, changes=changes), \
                        patch.object(adapter_module.usb, 'devices', return_value=[observed]), \
                        patch('orchard.state.operation_lock', return_value=nullcontext()), \
                        patch.object(build, 'configured_device', side_effect=AssertionError('installer read build config')), \
                        patch.object(service, 'ask') as prompt, \
                        patch.object(service, 'resolve') as download, \
                        patch.object(adapter, 'boot') as boot, \
                        self.assertRaisesRegex(ValueError, 'not one Orchard supports'):
                    service.install('unused-manifest', progress=Mock())
                prompt.assert_not_called()
                download.assert_not_called()
                boot.assert_not_called()


class LoaderPatchLayout(unittest.TestCase):
    def test_layered_m1n1_patches_count(self):
        with tempfile.TemporaryDirectory() as directory:
            hardware = Path(directory)
            (hardware / 'boot/m1n1/patches/orchard').mkdir(parents=True)
            self.assertFalse(build.has_loader_patches(hardware))
            (hardware / 'boot/m1n1/patches/orchard/0001-fill-in.patch').write_text('')
            self.assertTrue(build.has_loader_patches(hardware))


class RecipeInterpreter(unittest.TestCase):
    def test_hardware_recipes_run_python_as_orchard_itself(self):
        # Recipes call $ORCHARD_PYTHON; PATH's python3 may be too old (macOS: 3.9).
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        script = root / 'recipe'
        script.write_text('#!/bin/sh\nprintf %s "$ORCHARD_PYTHON:$EXTRA" > "$1"\n')
        script.chmod(0o755)
        with patch('sys.stdout'):
            build.command(script, root / 'default')
            build.command(script, root / 'explicit', env={**os.environ, 'EXTRA': 'kept'})
        self.assertEqual((root / 'default').read_text(), sys.executable + ':')
        self.assertEqual((root / 'explicit').read_text(), sys.executable + ':kept')

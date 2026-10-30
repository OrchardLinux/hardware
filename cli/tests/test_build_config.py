"""Project build defaults, independent of supported-device registration."""
from contextlib import nullcontext
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard_cli import build, service
from orchard_cli import devices
from orchard_cli.apple import device as adapter_module

adapter = devices.load('j71b')


class BuildConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.example = self.root / 'examples/deep/first-boot'
        self.example.mkdir(parents=True)
        self.config = self.root / 'orchard.toml'

    def test_repository_default_applies_to_nested_examples(self):
        self.config.write_text('device = "unregistered-board"\n')
        self.assertEqual(build.configured_device(self.example), 'unregistered-board')
        self.assertEqual(build.configured_device(self.root), 'unregistered-board')

    def test_nearest_config_overrides_repository_default(self):
        self.config.write_text('device = "j71b"\n')
        (self.example / 'orchard.toml').write_text('device = "experimental"\n')
        self.assertEqual(build.configured_device(self.example), 'experimental')

    def test_no_config_selects_no_device(self):
        (self.root / '.git').mkdir()
        self.assertIsNone(build.configured_device(self.example))

    def test_does_not_inherit_another_repositorys_config(self):
        self.config.write_text('device = "unrelated"\n')
        (self.example / '.git').write_text('gitdir: elsewhere\n')
        self.assertIsNone(build.configured_device(self.example))

    def test_transport_defaults_on_and_can_be_turned_off(self):
        self.config.write_text('device = "j71b"\n')
        self.assertEqual(build.configured(self.example), {'device': 'j71b', 'transport': True})
        self.config.write_text('device = "j71b"\ntransport = false\n')
        self.assertEqual(build.configured(self.example), {'device': 'j71b', 'transport': False})

    def test_invalid_or_unused_settings_fail_instead_of_silently_defaulting(self):
        for content in ('', 'device = [', 'device = 123', 'device = "../escape"',
                        'device = ""', 'device = "-bad"', 'device = "j71b"\nunused = true', 'transport = false',
                        'device = "j71b"\ntransport = "no"'):
            with self.subTest(content=content):
                self.config.write_text(content)
                with self.assertRaisesRegex(ValueError, 'orchard.toml'):
                    build.configured_device(self.example)

    def test_normal_installer_still_rejects_chip_and_board_mismatches(self):
        for mode in ('dfu', 'pongo'):
            for changes in ({'chip': adapter.CHIP + 1}, {'board': adapter.BOARD + 1}):
                observed = dict(mode=mode, chip=adapter.CHIP, board=adapter.BOARD,
                                ecid=123, location='fake')
                observed.update(changes)
                with self.subTest(mode=mode, changes=changes), \
                        patch.object(adapter_module.usb, 'devices', return_value=[observed]), \
                        patch('orchard_cli.state.operation_lock', return_value=nullcontext()), \
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
        # Recipes call $ORCHARD_PYTHON; PATH's python3 may lack orchard_hardware (macOS: 3.9).
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        script = root / 'recipe'
        script.write_text('#!/bin/sh\nprintf %s "$ORCHARD_PYTHON:$EXTRA" > "$1"\n')
        script.chmod(0o755)
        with patch('sys.stdout'):
            build.command(script, root / 'default')
            build.command(script, root / 'explicit', env={**os.environ, 'EXTRA': 'kept'})
        self.assertEqual((root / 'default').read_text(), sys.executable + ':')
        self.assertEqual((root / 'explicit').read_text(), sys.executable + ':kept')

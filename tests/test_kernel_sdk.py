"""CLI-owned SDK handling works with the unmodified hardware recipes."""
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

from orchard import hardware as hardware_data
from orchard import build, kernel, kernel_sdk

import hardware_layout


class KernelSDKTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.hardware = self.root / 'hardware'
        hardware_layout.write(self.hardware)
        (self.hardware / 'kernel/configs/apple/config').write_text('config\n')
        self.original_hardware = hardware_data.ROOT

    def compiler_output(self):
        output = self.root / 'compiler-output'
        for directory in kernel_sdk.DIRECTORIES:
            (output / directory).mkdir(parents=True, exist_ok=True)
        for name in kernel_sdk.REQUIRED:
            path = output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('prerequisite\n')
        (output / '.config').write_text('config\n')
        (output / 'include/config/kernel.release').write_text('test-release\n')
        return output

    def test_sdk_exports_prerequisites_not_full_objects_or_source_links(self):
        compiler = self.compiler_output()
        (compiler / 'vmlinux').write_text('large kernel object')
        (compiler / 'source').symlink_to('/src')
        sdk = self.root / 'sdk'
        kernel_sdk.export(compiler, sdk)
        for name in kernel_sdk.REQUIRED:
            self.assertTrue((sdk / name).is_file())
        self.assertFalse((sdk / 'vmlinux').exists())
        self.assertFalse((sdk / 'source').is_symlink())
        (compiler / 'Module.symvers').unlink()
        with self.assertRaises(ValueError):
            kernel_sdk.export(compiler, self.root / 'bad-sdk')

    def test_module_only_build_uses_original_hardware_api_and_private_sdk(self):
        compiler = self.compiler_output()
        base = self.root / 'base'
        kernel_sdk.export(compiler, base / 'sdk')
        (base / 'kernel.release').write_text('test-release\n')
        (base / 'Image.gz').write_text('cached kernel')
        (base / 'inputs.json').write_text(json.dumps({'source_tree': 'tree'}))
        (base / 'dtbs').mkdir()
        (base / 'dtbs/t8010-j71b.dtb').write_text('device dtb')
        modules = base / 'lib/modules/test-release/kernel/drivers'
        modules.mkdir(parents=True)
        (modules / 'keep.ko').write_text('cached upstream module')
        (modules / 'replace.ko').write_text('old module')
        source = self.root / 'source'
        source.mkdir()
        kernel.work_directory(self.hardware, 'j71b').mkdir(parents=True)
        (self.hardware / 'kernel/modules/j71b').mkdir(parents=True)
        script = self.original_hardware / 'kernel/stage-modules.py'
        spec = importlib.util.spec_from_file_location('old_stage_modules', script)
        stage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(stage)
        snapshot = {str(p.relative_to(base)): build.digest(p) for p in base.rglob('*') if p.is_file()}
        calls = []
        def command(*argv, **kwargs):
            calls.append(argv)
            self.assertNotIn('--base', argv)
            self.assertNotIn('--base-only', argv)
            self.assertNotIn('--base-modules', argv)
            if Path(argv[0]) == self.hardware / 'kernel/build-modules':
                sdk = Path(argv[argv.index('--build') + 1])
                self.assertNotEqual(sdk, base / 'sdk')
                self.assertTrue((sdk / 'Module.symvers').is_file())
                self.assertEqual((sdk / 'drivers/keep.ko').read_text(), 'cached upstream module')
                external = Path(argv[argv.index('--output') + 1])
                external.mkdir()
                (external / 'replace.ko').write_text('new external module')
            elif len(argv) > 1 and Path(argv[1]) == self.hardware / 'kernel/stage-modules.py':
                with patch('sys.argv', list(map(str, argv[1:]))), \
                        patch.object(stage.buildenv, 'native', return_value=True), \
                        patch.object(stage.subprocess, 'run'):
                    stage.main()
        output = self.root / 'result'
        with patch.object(kernel, 'prepare_source', return_value=(source, compiler)), \
                patch.object(kernel.sources, 'working_tree', return_value='tree'), \
                patch.object(build, 'command', side_effect=command):
            kernel.build_device(self.hardware, self.root / 'cache', 'j71b', base, output)
        result = output / 'lib/modules/test-release'
        self.assertEqual((result / 'kernel/drivers/keep.ko').read_text(), 'cached upstream module')
        self.assertEqual((result / 'extra/replace.ko').read_text(), 'new external module')
        self.assertFalse((result / 'kernel/drivers/replace.ko').exists())
        self.assertEqual((output / 'Image.gz').read_text(), 'cached kernel')
        self.assertEqual((output / 'j71b.dtb').read_text(), 'device dtb')
        self.assertEqual(snapshot, {str(p.relative_to(base)): build.digest(p)
                                    for p in base.rglob('*') if p.is_file()})
        self.assertFalse(any(Path(args[0]) == self.hardware / 'kernel/build' for args in calls))


if __name__ == '__main__':
    unittest.main()

"""The module staging recipe supports native CI without changing local Podman."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'kernel/stage-modules.py'


class ModuleStaging(unittest.TestCase):
    def test_native_depmod_never_requires_podman(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            build = root / 'build'
            (build / 'include/config').mkdir(parents=True)
            (build / 'include/config/kernel.release').write_text('test-release\n')
            for name in ('modules.builtin', 'modules.builtin.modinfo'):
                (build / name).write_text('')
            (build / 'test.ko').write_bytes(b'test-module')
            spec = importlib.util.spec_from_file_location('stage_modules', SCRIPT)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            output = root / 'stage'
            with mock.patch.object(sys, 'argv', [str(SCRIPT), '--build', str(build),
                                                '--output', str(output)]), \
                    mock.patch.object(module.buildenv, 'native', return_value=True), \
                    mock.patch.object(module.shutil, 'which') as which, \
                    mock.patch.object(module.subprocess, 'run') as run:
                module.main()
            which.assert_not_called()
            run.assert_called_once_with(['depmod', '-b', str(output.resolve()), 'test-release'],
                                        check=True)
            self.assertEqual((output / 'lib/modules/test-release/kernel/test.ko').read_bytes(),
                             b'test-module')

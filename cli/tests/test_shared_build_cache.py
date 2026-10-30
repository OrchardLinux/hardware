"""Cross-checkout reuse with real local Git and mocked compiler recipes."""
from contextlib import redirect_stdout
import io
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from orchard_cli import build, sources, kernel

import hardware_layout


class SharedBuildCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / 'shared-cache'
        self.upstream = self.root / 'upstream'
        self.upstream.mkdir()
        import subprocess
        subprocess.run(['git', 'init', '-q', str(self.upstream)], check=True)
        (self.upstream / 'driver.c').write_text('upstream driver\n')
        sources.git(self.upstream, 'add', '.')
        sources.git(self.upstream, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
        self.commit = sources.git(self.upstream, 'rev-parse', 'HEAD').decode()
        self.one, self.two = self.root / 'one/hardware', self.root / 'two/hardware'
        for hardware in (self.one, self.two):
            for name, text in {
                'kernel/build': 'producer', 'kernel/build-modules': 'external producer',
                'kernel/stage-modules.py': 'stage',
                'kernel/modules/j71b/power/driver.c': 'external driver',
                'kernel/upstream.lock': f'url={self.upstream.as_uri()}\ncommit={self.commit}\n',
            }.items():
                path = hardware / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            hardware_layout.write(hardware)
        self.overrides = {}
        for name in ('busybox', 'pongo', 'loader'):
            path = self.root / name
            path.write_text('explicit local component')
            self.overrides[name] = path
        self.calls = []

    def produce(self, hardware, cache, device, output, base=None):
        self.assertEqual(cache, self.cache)
        self.assertEqual(device, 'j71b')
        source = hardware / 'kernel/.work/apple/source'
        if not source.exists():
            sources.checkout(self.upstream.as_uri(), self.commit, source, cache=self.cache)
        output.mkdir(parents=True)
        if base is None:
            self.calls.append('kernel-base')
            (output / 'Image.gz').write_text(sources.working_tree(source))
            (output / 'kernel.release').write_text('test-release\n')
            module_tree = output / 'lib/modules/test-release'
            module_tree.mkdir(parents=True)
            (module_tree / 'modules.dep').write_text('upstream.ko:\n')
            (module_tree / 'upstream.ko').write_text('upstream module')
            (output / 'sdk').mkdir()
            (output / 'sdk/Module.symvers').write_text('matching symbols')
            (output / 'dtbs').mkdir()
            for dtb in ('t8010-j71b', 't8010-j72b'):
                (output / 'dtbs' / (dtb + '.dtb')).write_text(dtb)
        else:
            self.calls.append('external-modules-and-dtb')
            self.assertTrue((base / 'sdk/Module.symvers').is_file())
            for name in ('Image.gz', 'kernel.release'):
                shutil.copyfile(base / name, output / name)
            shutil.copytree(base / 'lib', output / 'lib')
            (output / 'lib/modules/test-release/external.ko').write_text(
                (hardware / 'kernel/modules/j71b/power/driver.c').read_text())
            dtb = kernel.layout(hardware, device)[1]
            shutil.copyfile(base / 'dtbs' / (dtb + '.dtb'), output / 'j71b.dtb')

    def prepare(self, hardware):
        with patch.object(kernel, 'build_base', side_effect=self.produce), \
                patch.object(kernel, 'build_device', side_effect=lambda hw, cache, device, base, out:
                             self.produce(hw, cache, device, out, base)):
            return build.prepare_components(hardware, self.cache, device='j71b', **self.overrides)

    def test_fresh_second_checkout_neither_downloads_checks_out_nor_compiles_linux(self):
        first = self.prepare(self.one)
        self.assertEqual(self.calls, ['kernel-base', 'external-modules-and-dtb'])
        # No upstream access possible on the second build.
        self.upstream.rename(self.root / 'upstream-offline')
        second = self.prepare(self.two)
        self.assertEqual(first['kernel'], second['kernel'])
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.two / 'kernel/.work').exists())

    def test_kernel_only_caller_reuses_ram_build_without_checkout_or_other_producers(self):
        first = self.prepare(self.one)
        self.upstream.rename(self.root / 'upstream-offline')
        with patch.object(kernel, 'build_base', side_effect=AssertionError('base rebuilt')), \
                patch.object(kernel, 'build_device', side_effect=AssertionError('modules rebuilt')), \
                patch.object(sources, 'checkout', side_effect=AssertionError('source checked out')), \
                patch.object(build, 'command', side_effect=AssertionError('unrelated producer invoked')):
            second = build.prepare_kernel(self.two, self.cache, device='j71b')
        self.assertEqual(first['kernel'], second)
        self.assertFalse((self.two / 'kernel/.work').exists())

    def test_ram_build_reuses_kernel_only_build(self):
        with patch.object(kernel, 'build_base', side_effect=self.produce), \
                patch.object(kernel, 'build_device', side_effect=lambda hw, cache, device, base, out:
                             self.produce(hw, cache, device, out, base)):
            first = build.prepare_kernel(self.one, self.cache, device='j71b')
        second = self.prepare(self.two)
        self.assertEqual(first, second['kernel'])
        self.assertEqual(self.calls, ['kernel-base', 'external-modules-and-dtb'])
        self.assertFalse((self.two / 'kernel/.work').exists())

    def test_kernel_defaults_to_active_hardware_and_shared_cache_environment(self):
        import orchard_hardware
        first = self.prepare(self.one)
        with patch.object(orchard_hardware, '__file__', str(self.two / '__init__.py')), \
                patch.dict(os.environ, {'ORCHARD_CACHE_DIR': str(self.cache)}), \
                patch.object(kernel, 'build_base', side_effect=AssertionError('base rebuilt')), \
                patch.object(kernel, 'build_device', side_effect=AssertionError('modules rebuilt')):
            second = build.prepare_kernel(device='j71b')
        self.assertEqual(first['kernel'], second)
        self.assertFalse((self.two / 'kernel/.work').exists())

    def test_cache_misses_and_hits_are_visible_without_changing_identity(self):
        with redirect_stdout(io.StringIO()) as first_log:
            first = self.prepare(self.one)
        self.assertIn('Building kernel-base-apple (cache miss):', first_log.getvalue())
        self.assertIn('Building kernel-j71b (cache miss):', first_log.getvalue())
        with redirect_stdout(io.StringIO()) as second_log:
            second = build.prepare_kernel(self.two, self.cache, device='j71b')
        self.assertEqual(first['kernel'], second)
        self.assertIn('Reusing kernel-base-apple:', second_log.getvalue())
        self.assertIn('Reusing kernel-j71b:', second_log.getvalue())
        self.assertNotIn('Building ', second_log.getvalue())
        self.assertEqual(self.calls, ['kernel-base', 'external-modules-and-dtb'])

    def test_reused_cache_is_still_hash_verified(self):
        first = self.prepare(self.one)
        base = next((self.cache / 'components-v2/kernel-base-apple').iterdir()) / 'kernel'
        (base / 'Image.gz').write_bytes(b'corrupted')
        shutil.rmtree(first['kernel'].parent)
        with patch.object(kernel, 'build_base', side_effect=AssertionError('silently rebuilt')), \
                self.assertRaisesRegex(ValueError, 'Cached component changed'):
            build.prepare_kernel(self.two, self.cache, device='j71b')

    def test_missing_recipe_fails_before_any_checkout_or_producer(self):
        with patch.object(sources, 'checkout', side_effect=AssertionError('checked out source')), \
                patch.object(build, 'command', side_effect=AssertionError('started producer')), \
                self.assertRaisesRegex(ValueError, 'Missing hardware recipe input'):
            build.prepare_components(self.one, self.cache, device='new-board')
        self.assertFalse(self.cache.exists())

    def test_external_driver_change_reuses_kernel_base_and_sdk(self):
        first = self.prepare(self.one)
        (self.two / 'kernel/modules/j71b/power/driver.c').write_text('changed external driver')
        second = self.prepare(self.two)
        self.assertEqual(self.calls, ['kernel-base', 'external-modules-and-dtb', 'external-modules-and-dtb'])
        self.assertNotEqual(first['kernel'], second['kernel'])
        self.assertEqual(build.digest(first['kernel'] / 'Image.gz'), build.digest(second['kernel'] / 'Image.gz'))
        self.assertEqual((second['kernel'] / 'lib/modules/test-release/external.ko').read_text(),
                         'changed external driver')

    def test_readme_other_device_and_firmware_changes_do_not_rebuild_kernel(self):
        first = self.prepare(self.one)
        for relative in ('README.md', 'devices/j71b/firmware.json', 'kernel/configs/other/config',
                         'kernel/modules/other/driver.c'):
            path = self.two / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('unrelated change')
        second = self.prepare(self.two)
        self.assertEqual(first['kernel'], second['kernel'])
        self.assertEqual(len(self.calls), 2)

    def test_config_and_local_in_tree_edits_rebuild_the_base_without_reset(self):
        self.prepare(self.one)
        (self.two / 'kernel/configs/apple/config').write_text('changed config')
        self.prepare(self.two)
        self.assertEqual(self.calls.count('kernel-base'), 2)
        source = self.one / 'kernel/.work/apple/source'
        (source / 'driver.c').write_text('local experimental kernel driver\n')
        self.prepare(self.one)
        self.assertEqual(self.calls.count('kernel-base'), 3)
        self.assertEqual((source / 'driver.c').read_text(), 'local experimental kernel driver\n')

    def test_inputs_changed_during_build_are_not_published(self):
        values = ['before']
        def produce(stage):
            (stage / 'artifact').write_text('built from old input')
            values[0] = 'after'
        with self.assertRaises(ValueError):
            build.cached_component(self.cache, 'race', lambda: values[0], produce)
        self.assertFalse((self.cache / 'race/before').exists())
        self.assertFalse((self.cache / 'race/after').exists())

    def test_dtb_selection_only_rebuilds_device_layer(self):
        self.prepare(self.one)
        hardware_layout.write(self.two, dtb='t8010-j72b')
        second = self.prepare(self.two)
        self.assertEqual((second['kernel'] / 'j71b.dtb').read_text(), 't8010-j72b')
        self.assertEqual(self.calls.count('kernel-base'), 1)
        self.assertEqual(self.calls.count('external-modules-and-dtb'), 2)


if __name__ == '__main__':
    unittest.main()

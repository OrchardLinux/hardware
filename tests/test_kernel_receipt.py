"""The output receipt binds the image, device tree and complete module tree."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('kernel_receipt', ROOT / 'kernel/write-receipt.py')
producer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(producer)


class KernelReceipt(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.output = self.root / 'kernel'
        self.release = '7.3.0-rc1-orchard-apple-4k'
        self.files = {
            'Image.gz': b'image',
            'kernel.release': (self.release + '\n').encode(),
            'n61.dtb': b'device tree',
            'dtbs/t7000-n61.dtb': b'device tree',
            'dtbs/t7001-j81.dtb': b'other device tree',
            f'lib/modules/{self.release}/kernel/driver.ko': b'module',
            f'lib/modules/{self.release}/modules.dep': b'kernel/driver.ko:\n',
        }
        for name, contents in self.files.items():
            path = self.output / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(contents)
        self.patch_list = self.root / 'applied-patches'
        self.recipe = self.root / 'hardware'
        for name in ('__init__.py', 'buildenv.py', 'kernel/Containerfile',
                     'kernel/install-toolchain', 'kernel/build', 'kernel/build-modules',
                     'kernel/stage-modules.py', 'kernel/write-receipt.py',
                     'kernel/upstream.lock', 'kernel/sources.toml', 'kernel/series.py',
                     'kernel/series/apple-4k', 'devices/n61/firmware.json'):
            path = self.recipe / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
        descriptor = self.recipe / 'devices/n61/device.toml'
        descriptor.write_text('[firmware]\nrecipe = "firmware.json"\n')
        self.device_module = self.recipe / 'kernel/modules/n61/of-overlay/Makefile'
        self.device_module.parent.mkdir(parents=True)
        self.device_module.write_text('obj-m += overlay.o\n')
        patch = self.recipe / 'kernel/patches/test.patch'
        patch.parent.mkdir(parents=True, exist_ok=True)
        patch.write_bytes(b'example patch\n')
        blob = hashlib.sha1(b'blob ' + str(patch.stat().st_size).encode() + b'\0' + patch.read_bytes()).hexdigest()
        self.patch_list.write_text(blob + ' patches/test.patch\n')
        self.config = self.recipe / 'kernel/configs/apple-4k/config'
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text('CONFIG_ARM64_4K_PAGES=y\n')
        self.recipe_file = self.root / 'recipe.json'
        snapshot = producer.recipe_snapshot(self.recipe, 'n61', 'apple-4k', self.patch_list)
        self.recipe_file.write_text(json.dumps(snapshot))

    def produce(self, **overrides):
        args = dict(directory=self.output, device='n61', platform='apple-4k',
                    dtb='t7000-n61', source_commit='a' * 40, source_tree='b' * 40,
                    prepared_tree='c' * 40, patch_list=self.patch_list, config=self.config,
                    recipe_root=self.recipe, recipe_file=self.recipe_file)
        args.update(overrides)
        return producer.write_receipt(**args)

    def test_receipt_covers_each_output_and_source_state(self):
        receipt = self.produce()
        self.assertEqual(json.loads((self.output / 'receipt.json').read_text()), receipt)
        self.assertEqual((receipt['device'], receipt['platform'], receipt['dtb']),
                         ('n61', 'apple-4k', 't7000-n61'))
        self.assertEqual(receipt['kernel_release'], self.release)
        self.assertEqual(receipt['source']['tree'], 'b' * 40)
        self.assertEqual(receipt['source']['prepared_tree'], 'c' * 40)
        self.assertIn('devices/n61/firmware.json', receipt['hardware_recipe']['files'])
        self.assertIn('kernel/patches/test.patch', receipt['hardware_recipe']['files'])
        self.assertIn('kernel/modules/n61/of-overlay/Makefile', receipt['hardware_recipe']['files'])
        self.assertEqual(set(receipt['files']), set(self.files))
        for name, contents in self.files.items():
            with self.subTest(name=name):
                self.assertEqual(receipt['files'][name], {
                    'bytes': len(contents), 'sha256': hashlib.sha256(contents).hexdigest()})

    def test_rejects_mismatched_platform_or_device_tree(self):
        with self.assertRaisesRegex(ValueError, 'platform'):
            self.produce(platform='apple-16k')
        (self.output / 'n61.dtb').write_bytes(b'different')
        with self.assertRaisesRegex(ValueError, 'Device DTB'):
            self.produce()

    def test_rejects_missing_modules_and_linked_files(self):
        (self.output / f'lib/modules/{self.release}/kernel/driver.ko').unlink()
        with self.assertRaisesRegex(ValueError, 'Missing kernel modules'):
            self.produce()
        module = self.output / f'lib/modules/{self.release}/kernel/driver.ko'
        module.symlink_to(self.output / 'Image.gz')
        with self.assertRaisesRegex(ValueError, 'Linked kernel output'):
            self.produce()

    def test_rejects_recipe_changes_after_capture(self):
        self.device_module.write_text('obj-m += different.o\n')
        with self.assertRaisesRegex(ValueError, 'Hardware recipe changed'):
            self.produce()


if __name__ == '__main__':
    unittest.main()

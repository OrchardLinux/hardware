"""Archive audits catch missing inputs and private scratch without extracting files."""
import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('audit_packages', ROOT / 'ci/audit_packages.py')
packages = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packages)


class PackageArchives(unittest.TestCase):
    def fixture(self, root):
        source = root / 'source'
        for name in ('__init__.py', 'buildenv.py', 'kernel/Containerfile',
                     'kernel/install-toolchain', 'kernel/build', 'kernel/build-modules',
                     'kernel/stage-modules.py', 'kernel/write-receipt.py',
                     'kernel/series.py', 'kernel/sources.toml', 'kernel/upstream.lock',
                     'boot/m1n1/Containerfile', 'boot/m1n1/install-toolchain',
                     'boot/m1n1/build', 'boot/m1n1/series', 'boot/m1n1/sources.toml',
                     'boot/m1n1/upstream.lock',
                     'features.toml', 'image/runtime.py', 'boot/quiet/m1n1.patch',
                     'boot/initramfs/recovery/init',
                     'devices/n61/device.toml', 'kernel/series/apple-4k',
                     'kernel/configs/apple-4k/config', 'kernel/patches/drivers/input/test.patch',
                     'boot/m1n1/patches/prox.patch', 'kernel/devicetree/n61/touch.dtso',
                     'kernel/modules/n61/of-overlay/helper.c', 'docs/bring-up/n61-touch.md'):
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        return source

    def wheel(self, root, source, *, extra=None, omit=None, changed=None):
        wheel = root / 'hardware.whl'
        with zipfile.ZipFile(wheel, 'w') as archive:
            for path in packages.resources(source):
                relative = path.relative_to(source).as_posix()
                if relative != omit:
                    archive.writestr('orchard_hardware/' + relative,
                                     b'changed' if relative == changed else path.read_bytes())
            if extra:
                archive.writestr(extra, b'private')
        return wheel

    def test_wheel_resources_are_checked_by_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root)
            self.assertEqual(packages.audit(self.wheel(root, source), source)['verified_resources'], 29)
            for options, error in (({'omit': 'kernel/series.py'}, 'Missing'),
                                   ({'changed': 'kernel/configs/apple-4k/config'}, 'Changed')):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, error):
                    packages.audit(self.wheel(root, source, **options), source)

    def test_development_sources_and_loader_patches_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root)
            for name in ('boot/m1n1/patches/prox.patch',
                         'kernel/devicetree/n61/touch.dtso',
                         'kernel/modules/n61/of-overlay/helper.c',
                         'docs/bring-up/n61-touch.md'):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Missing'):
                    packages.audit(self.wheel(root, source, omit=name), source)

    def test_existing_runtime_and_image_recipes_are_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root)
            for name in ('features.toml', 'image/runtime.py', 'boot/quiet/m1n1.patch',
                         'boot/initramfs/recovery/init'):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Missing'):
                    packages.audit(self.wheel(root, source, omit=name), source)

    def test_wheel_refuses_scratch_and_links(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root)
            for name in ('orchard_hardware/.work/private.bin',
                         'orchard_hardware/kernel/.work/private.md'):
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Build scratch'):
                    packages.audit(self.wheel(root, source, extra=name), source)
            wheel = self.wheel(root, source)
            with zipfile.ZipFile(wheel, 'a') as archive:
                link = zipfile.ZipInfo('orchard_hardware/link')
                link.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(link, '/private/owner-input')
            with self.assertRaisesRegex(ValueError, 'Archive link'):
                packages.audit(wheel, source)

    def test_sdist_retains_resources_and_refuses_root_or_kernel_scratch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.fixture(root)
            archive = root / 'hardware.tar.gz'
            for extra in (None, '.work/private.md', 'kernel/.work/private.md'):
                with tarfile.open(archive, 'w:gz') as output:
                    for path in packages.resources(source):
                        output.add(path, arcname='hardware-1/' + path.relative_to(source).as_posix())
                    if extra:
                        entry = tarfile.TarInfo('hardware-1/' + extra)
                        entry.size = 7
                        output.addfile(entry, io.BytesIO(b'private'))
                if extra:
                    with self.subTest(extra=extra), self.assertRaisesRegex(ValueError, 'Build scratch'):
                        packages.audit(archive, source)
                else:
                    self.assertEqual(packages.audit(archive, source)['verified_resources'], 29)


if __name__ == '__main__':
    unittest.main()

"""Public component archives retain integrity and reject changed inputs."""
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'ci' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


package_components = load('package_components')
launcher_inputs = load('launcher_inputs')


class ComponentBundle(unittest.TestCase):
    def fixtures(self, root):
        for name in ('kernel/Image.gz', 'kernel/j71b.dtb', 'kernel/kernel.release',
                     'kernel/lib/modules/test/extra/test.ko', 'boot/busybox',
                     'boot/dropbear', 'boot/dropbearkey', 'quiet/m1n1.bin', 'quiet/pongo.bin'):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
            path.chmod(0o755)

    def test_package_and_launcher_inputs_bind_files_and_quiet_patches(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / 'components'
            self.fixtures(root)
            package_components.package(root, parent / 'bundle.tar.gz')
            with tarfile.open(parent / 'bundle.tar.gz') as archive:
                member = archive.getmember('components/boot/dropbear')
                self.assertEqual(member.mode & 0o777, 0o755)
            launcher_inputs.prepare(root, parent / 'inputs.json')
            inputs = json.loads((parent / 'inputs.json').read_text())
            self.assertEqual(Path(inputs['files']['loader']['path']), (root / 'quiet/m1n1.bin').resolve())
            from orchard_hardware.image.launcher import verify_quiet_boot
            verify_quiet_boot(root / 'quiet/quiet-boot.json', inputs)
            (root / 'kernel/Image.gz').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'Component differs'):
                launcher_inputs.prepare(root, parent / 'changed.json')

    def test_missing_modules_or_links_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / 'components'
            self.fixtures(root)
            module = root / 'kernel/lib/modules/test/extra/test.ko'
            module.unlink()
            with self.assertRaisesRegex(ValueError, 'modules are missing'):
                package_components.package(root, parent / 'bundle.tar.gz')
            module.write_bytes(b'module')
            (root / 'linked').symlink_to(root / 'kernel/Image.gz')
            with self.assertRaisesRegex(ValueError, 'Linked component'):
                package_components.package(root, parent / 'bundle.tar.gz')


if __name__ == '__main__':
    unittest.main()

"""Device-profile selection in the public runtime producer and the shared device API."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from orchard_hardware.image import runtime

HARDWARE = Path(__file__).resolve().parents[1]
RELEASE = '7.3.0-orchard-test'
COMMON = {'archive.py', 'adt.py', 'device.py', 'handoff.py', 'hardware', 'identity.py',
          'image_policy.py', 'launch.py', 'ssh_access.py', 'staging.py', 'storage.py'}


class RuntimeProfiles(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.modules = root / 'kernel/lib/modules'
        (self.modules / RELEASE / 'kernel').mkdir(parents=True)
        for name in ('virt-dma', 'apple-sio-core', 'apple-sio-dma', 'j71b_dma_bindings',
                     'j71b_sio_denied_test', 'j71b_spi_dma_reset', 'j71b_touch_start', 'j71b_z2'):
            (self.modules / RELEASE / 'kernel' / (name + '.ko')).write_bytes(name.encode())
        (root / 'kernel/kernel.release').write_text(RELEASE + '\n')
        self.policy = root / 'policy'
        self.policy.mkdir()
        (self.policy / 'image_policy.py').write_text('INIT = "/sbin/init"\n')

    def runtime_names(self, profile):
        release, result = runtime.files(self.modules, self.policy, profile)
        self.assertEqual(release, RELEASE)
        prefix = 'usr/libexec/orchard-' + profile + '/'
        wrapper = result['usr/bin/orchard-device'][0].decode()
        self.assertEqual(wrapper, '#!/bin/sh\nexec /usr/bin/python3 /' + prefix + 'device.py "$@"\n')
        self.assertFalse([name for name in result
                          if name.startswith('usr/libexec/orchard-') and not name.startswith(prefix)])
        return {name[len(prefix):] for name in result if name.startswith(prefix)}, result

    def test_j71b_layout_is_unchanged(self):
        names, result = self.runtime_names('j71b')
        roles = {name.split('/', 1)[0] for name in names if '/' in name}
        self.assertEqual(roles, {'touch-start', 'touch-runtime'})
        self.assertEqual({name for name in names if '/' not in name}, COMMON | {'touch-activate'})
        self.assertIn('usr/libexec/j71b-check-platform', result)
        self.assertIn('usr/libexec/j71b-charge-lease', result)

    def test_j71t_uses_shared_api_with_its_hardware_preparation(self):
        names, result = self.runtime_names('j71t')
        self.assertEqual(names, COMMON)
        self.assertEqual(result['usr/libexec/orchard-j71t/hardware'][0],
                         (HARDWARE / 'runtime/j71t/phase2-hardware').read_bytes())
        self.assertEqual(result['usr/libexec/orchard-j71t/device.py'][0],
                         (HARDWARE / 'runtime/j71b/device.py').read_bytes())
        self.assertNotIn(b'@KERNEL_RELEASE@', result['usr/libexec/j71t-check-platform'][0])
        self.assertNotIn('usr/libexec/j71b-charge-lease', result)
        self.assertIn('usr/lib/firmware/regulatory.db', result)

    def test_unknown_profile_is_refused(self):
        with self.assertRaises(ValueError):
            runtime.files(self.modules, self.policy, 'j99z')

    def test_device_api_profile_follows_install_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'orchard-j71t'
            directory.mkdir()
            for name in ('identity.py', 'staging.py'):
                shutil.copyfile(HARDWARE / 'runtime/j71b' / name, directory / name)
            profiles = []
            for name in ('identity', 'staging'):
                spec = importlib.util.spec_from_file_location('profile_' + name, directory / (name + '.py'))
                module = importlib.util.module_from_spec(spec)
                with patch.dict(sys.modules, {'adt': types.SimpleNamespace(validate=None)}):
                    spec.loader.exec_module(module)
                profiles.append(module.PROFILE)
            self.assertEqual(profiles, ['j71t', 'j71t'])

    def test_every_profile_has_recovery_and_storage_fence(self):
        device = (HARDWARE / 'runtime/j71b/device.py').read_text()
        for profile in runtime.PROFILES:
            recovery = HARDWARE / 'boot/initramfs' / profile / 'recovery'
            for name in ('init', 'usb-gadget', 'boot.json', 'boot-verbose.json'):
                self.assertTrue((recovery / name).is_file(), profile + ': ' + name)
            self.assertTrue((HARDWARE / 'runtime' / profile / 'phase2-hardware').is_file())
            self.assertIn("'" + profile + "': ('/sys/module/nvme/parameters/", device)


if __name__ == '__main__':
    unittest.main()

"""orchard's platform layer: kernel, DTBs, modules and /usr/lib defaults for every root image."""
import gzip
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from orchard.hardware import ROOT
from orchard.image import platform

RELEASE = '6.18.0-orchard-apple-16k'


class PlatformLayer(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.work = Path(work.name)
        self.bundle = self.work / 'kernel'
        modules = self.bundle / 'lib/modules' / RELEASE
        (modules / 'kernel/drivers/nvme/host').mkdir(parents=True)
        (modules / 'modules.dep').write_text('kernel/drivers/x.ko:\n'
                                             'kernel/drivers/nvme/host/nvme-core.ko:\n'
                                             'kernel/drivers/nvme/host/nvme.ko: kernel/drivers/nvme/host/nvme-core.ko\n')
        (modules / 'kernel/drivers/x.ko').write_bytes(b'module')
        for name in ('nvme', 'nvme-core'):
            (modules / f'kernel/drivers/nvme/host/{name}.ko').write_bytes(name.encode())
        self.busybox = self.work / 'busybox'
        self.busybox.write_bytes(b'static busybox')
        (self.bundle / 'kernel.release').write_text(RELEASE + '\n')
        (self.bundle / 'Image.gz').write_bytes(b'kernel image')
        (self.bundle / 'dtbs').mkdir()
        self.boards = platform.boards('apple-16k')
        for dtb in self.boards.values():
            (self.bundle / 'dtbs' / (dtb + '.dtb')).write_bytes(dtb.encode())

    def test_every_board_of_the_platform_and_nothing_outside_usr(self):
        self.assertIn('j71b', self.boards)
        self.assertIn('apple-4k', platform.platforms())
        receipt = platform.build('apple-16k', self.work, bundle=self.bundle, busybox=self.busybox)
        self.assertEqual((receipt['file'], receipt['kernel_release']), ('platform-apple-16k.tar.gz', RELEASE))
        self.assertEqual(receipt['boards'], sorted(self.boards))
        with tarfile.open(self.work / receipt['file']) as archive:
            members = archive.getmembers()
            names = {member.name for member in members}
            description = json.load(archive.extractfile('usr/lib/orchard/platform.json'))
        self.assertTrue(all(member.isfile() and member.name.startswith('usr/') for member in members))
        self.assertEqual(receipt['members'], len(members))
        base = f'usr/lib/modules/{RELEASE}/'
        self.assertLessEqual({base + 'vmlinuz', base + 'initrd', base + 'modules.dep', base + 'kernel/drivers/x.ko',
                              base + 'dtb/t8010-j71b.dtb', 'usr/lib/firmware/regulatory.db',
                              'usr/lib/firmware/regulatory.db.p7s',
                              'usr/lib/NetworkManager/conf.d/30-orchard-usb.conf',
                              'usr/lib/orchard/platform.SHA256SUMS'}, names)
        self.assertEqual(len([name for name in names if '/dtb/' in name]), len(set(self.boards.values())))
        self.assertEqual(description, {'schema': 1, 'platform': 'apple-16k', 'kernel_release': RELEASE,
                                       'boards': dict(sorted(self.boards.items()))})

    def test_the_initrd_carries_busybox_and_the_storage_modules_only(self):
        import gzip, io
        from orchard.image.initramfs import record  # noqa: F401  (format reference)
        receipt = platform.build('apple-16k', self.work, bundle=self.bundle, busybox=self.busybox)
        with tarfile.open(self.work / receipt['file']) as archive:
            data = archive.extractfile(f'usr/lib/modules/{RELEASE}/initrd').read()
        names = {}
        stream = io.BytesIO(gzip.decompress(data))
        while True:
            header = stream.read(110)
            fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
            name = stream.read(fields[11])[:-1].decode()
            stream.read(-(110 + fields[11]) % 4)
            if name == 'TRAILER!!!':
                break
            names[name] = stream.read(fields[6])
            stream.read(-fields[6] % 4)
        base = f'lib/modules/{RELEASE}/'
        self.assertEqual(names['bin/busybox'], b'static busybox')
        self.assertEqual(names['init'], (ROOT / 'boot/initramfs/root/init').read_bytes())
        self.assertEqual(names['etc/orchard/modules'], b'nvme\n')
        self.assertEqual(sorted(name for name in names if name.endswith('.ko')),
                         [base + 'kernel/drivers/nvme/host/nvme-core.ko', base + 'kernel/drivers/nvme/host/nvme.ko'])
        self.assertEqual(names[base + 'modules.dep'],
                         b'kernel/drivers/nvme/host/nvme-core.ko:\n'
                         b'kernel/drivers/nvme/host/nvme.ko: kernel/drivers/nvme/host/nvme-core.ko\n')

    def test_the_same_inputs_give_the_same_archive(self):
        first, second = self.work / 'a', self.work / 'b'
        first.mkdir(), second.mkdir()
        self.assertEqual(platform.build('apple-16k', first, bundle=self.bundle, busybox=self.busybox)['sha256'],
                         platform.build('apple-16k', second, bundle=self.bundle, busybox=self.busybox)['sha256'])

    def test_a_missing_board_dtb_is_an_error(self):
        (self.bundle / 'dtbs/t8010-j71b.dtb').unlink()
        with self.assertRaisesRegex(ValueError, 'lacks t8010-j71b.dtb'):
            platform.build('apple-16k', self.work, bundle=self.bundle, busybox=self.busybox)

    def test_system_defaults_stay_under_usr_lib(self):
        defaults = ROOT / 'system/defaults'
        self.assertTrue(all(path.relative_to(defaults).as_posix().startswith('usr/lib/')
                            for path in defaults.rglob('*') if path.is_file()))

    def test_unknown_platform_lists_the_known_ones(self):
        with self.assertRaisesRegex(ValueError, 'Unknown platform apple-64k; known: apple-16k, apple-4k'):
            platform.boards('apple-64k')


if __name__ == '__main__':
    unittest.main()

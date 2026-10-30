"""orchard's initrd finds the root by reading the GPT: the kernel's uevent has no PARTUUID."""
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest
import uuid

INIT = Path(__file__).resolve().parents[1] / 'boot/initramfs/root/init'


def gpt(path, sector, guids):
    """A disk image with a GPT whose entries have GUIDS, entry array at LBA 2."""
    data = bytearray(sector * 40)
    header = bytearray(92)
    header[0:8] = b'EFI PART'
    struct.pack_into('<QII', header, 72, 2, 128, 128)
    data[sector:sector + 92] = header
    for index, guid in enumerate(guids):
        if guid:
            offset = 2 * sector + index * 128
            data[offset:offset + 16] = uuid.UUID('0fc63daf-8483-4772-8e79-3d69d8477de4').bytes_le
            data[offset + 16:offset + 32] = uuid.UUID(guid).bytes_le
    path.write_bytes(bytes(data))


class RootLookup(unittest.TestCase):
    def lookup(self, image, sector, number):
        function = re.search(r'^gpt_partuuid\(\) \{.*?^\}', INIT.read_text(), re.M | re.S).group(0)
        result = subprocess.run(['sh', '-c', function + '\ngpt_partuuid "$0" "$1" "$2"', str(image),
                                 str(sector), str(number)], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    def test_every_entry_reads_as_its_partuuid_for_both_sector_sizes(self):
        guids = [str(uuid.uuid4()) for _ in range(3)]
        with tempfile.TemporaryDirectory() as directory:
            for sector in (512, 4096):
                image = Path(directory) / f'disk-{sector}'
                gpt(image, sector, [guids[0], None, guids[1], guids[2]])
                with self.subTest(sector=sector):
                    self.assertEqual([self.lookup(image, sector, n) for n in (1, 2, 3, 4)],
                                     [guids[0], None, guids[1], guids[2]])

    def test_a_disk_without_gpt_matches_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'apfs'
            image.write_bytes(b'\0' * 4096 * 4)
            self.assertIsNone(self.lookup(image, 4096, 1))


if __name__ == '__main__':
    unittest.main()

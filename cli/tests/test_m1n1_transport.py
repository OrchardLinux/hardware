"""m1n1 proxy transport: framing checksums and storage load batching."""
import unittest

from orchard_cli.transports import m1n1


class Recorder(m1n1.M1n1Proxy):
    def __init__(self):
        super().__init__('/dev/null')
        self.staging, self.reads, self.copies = 0x1000_0000, [], []

    def read_into(self, lba, addr, count=1):
        self.reads.append((lba, addr, count))

    def call(self, opcode, *args, signed=False):
        if opcode == m1n1.P_MEMCPY8:
            self.copies.append(args)
        return 0


class Transport(unittest.TestCase):
    def test_checksum_matches_m1n1(self):
        # proxyclient's UartInterface.checksum for b'abc'
        self.assertEqual(m1n1.checksum(b'abc'), 3020375102)

    def test_aligned_runs_load_in_256_block_commands(self):
        proxy = Recorder()
        size = 625 * 4096 + 100
        loaded = proxy.load_runs([(16 * 4096, size)], 0x2000_0000)
        self.assertEqual(loaded, size)
        whole = [(lba, count) for lba, addr, count in proxy.reads if addr != proxy.staging]
        self.assertEqual(whole, [(16, 256), (272, 256), (528, 113)])
        self.assertEqual(proxy.reads[-1], (641, proxy.staging, 1))
        self.assertEqual(proxy.copies, [(0x2000_0000 + 625 * 4096, proxy.staging, 100)])

    def test_unaligned_starts_go_through_staging(self):
        proxy = Recorder()
        proxy.load_runs([(16 * 4096 + 512, 1000)], 0x2000_0000)
        self.assertEqual(proxy.reads, [(16, proxy.staging, 1)])
        self.assertEqual(proxy.copies, [(0x2000_0000, proxy.staging + 512, 1000)])


if __name__ == '__main__':
    unittest.main()

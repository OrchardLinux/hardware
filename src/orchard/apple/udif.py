"""Read-only UDIF (.dmg) reader: koly trailer, blkx tables, per-chunk decompression."""
import bisect
import plistlib
import struct
import zlib

from . import lzfse

SECTOR = 512


class Udif:
    def __init__(self, source):
        """source has read(offset, length) and size."""
        self.source = source
        koly = source.read(source.size - 512, 512)
        if koly[:4] != b'koly':
            raise ValueError('No koly trailer')
        xml_offset, xml_length = struct.unpack_from('>QQ', koly, 0xD8)
        plist = plistlib.loads(source.read(xml_offset, xml_length))
        self.chunks = []  # (first sector, sector count, kind, compressed offset, compressed length)
        self.partitions = []
        for entry in plist['resource-fork']['blkx']:
            mish = entry['Data']
            if mish[:4] != b'mish':
                raise ValueError('Bad blkx entry')
            first, count = struct.unpack_from('>QQ', mish, 8)
            data_offset = struct.unpack_from('>Q', mish, 24)[0]
            n = struct.unpack_from('>I', mish, 200)[0]
            self.partitions.append((entry.get('Name', ''), first, count))
            for i in range(n):
                kind, _, sector, sectors, coff, clen = struct.unpack_from('>IIQQQQ', mish, 204 + 40 * i)
                if kind in (0x7FFFFFFE, 0xFFFFFFFF) or sectors == 0:
                    continue
                self.chunks.append((first + sector, sectors, kind, data_offset + coff, clen))
        self.chunks.sort()
        self.starts = [c[0] for c in self.chunks]
        self.size = max(c[0] + c[1] for c in self.chunks) * SECTOR
        self.memo = {}
        self.inflated = 0

    def chunk(self, index):
        if index in self.memo:
            return self.memo[index]
        first, sectors, kind, coff, clen = self.chunks[index]
        size = sectors * SECTOR
        if kind in (0, 2):
            data = bytes(size)
        elif kind == 1:
            data = self.source.read(coff, clen)
        else:
            raw = self.source.read(coff, clen)
            if kind == 0x80000005:
                data = zlib.decompress(raw)
            elif kind == 0x80000007:
                data = lzfse.decompress(raw)
            else:
                raise ValueError(f'Unsupported UDIF chunk type {kind:#x}')
            self.inflated += len(data)
        if len(data) != size:
            raise ValueError('Chunk inflated to wrong size')
        if len(self.memo) > 64:
            self.memo.clear()
        self.memo[index] = data
        return data

    def read(self, offset, length):
        out = bytearray()
        while length:
            sector = offset // SECTOR
            i = bisect.bisect_right(self.starts, sector) - 1
            first, sectors = self.chunks[i][:2]
            if not first <= sector < first + sectors:
                raise ValueError('Offset not covered by any chunk')
            data = self.chunk(i)
            at = offset - first * SECTOR
            piece = data[at:at + length]
            out += piece
            offset += len(piece)
            length -= len(piece)
        return bytes(out)

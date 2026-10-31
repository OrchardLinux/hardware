"""Apple's LZSS ("complzss") kernelcache compression, after Okumura's LZSS as used by BootX."""
import struct
import zlib

N, F, THRESHOLD = 4096, 18, 2
HEADER = 0x180


def decompress(data):
    """A complzss container: 'comp' 'lzss' adler32, sizes (big-endian), padding, then the stream."""
    if data[:8] != b'complzss':
        raise ValueError('Not an LZSS container')
    checksum, size, packed = struct.unpack_from('>III', data, 8)
    src = data[HEADER:HEADER + packed]
    ring = bytearray(b' ' * (N - F) + bytes(F))
    r, flags, i, out = N - F, 0, 0, bytearray()
    end = len(src)
    while i < end:
        flags >>= 1
        if not flags & 0x100:
            flags = src[i] | 0xFF00
            i += 1
            if i >= end:
                break
        if flags & 1:
            c = src[i]
            i += 1
            out.append(c)
            ring[r] = c
            r = (r + 1) & (N - 1)
            continue
        if i + 1 >= end:
            break
        low, high = src[i], src[i + 1]
        i += 2
        position = low | ((high & 0xF0) << 4)
        for k in range((high & 0x0F) + THRESHOLD + 1):
            c = ring[(position + k) & (N - 1)]
            out.append(c)
            ring[r] = c
            r = (r + 1) & (N - 1)
    out = bytes(out[:size])
    if len(out) != size or zlib.adler32(out) != checksum:
        raise ValueError('LZSS kernelcache size or checksum differs')
    return out

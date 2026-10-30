"""Bounded legacy 64-bit Mach-O segment/section reader."""
import struct


def macho(blob, start=0):
    if start < 0 or start + 32 > len(blob):
        raise ValueError('Truncated Mach-O header')
    magic, _, _, filetype, count, total = struct.unpack_from('<6I', blob, start)
    if magic != 0xfeedfacf or count > 10000 or start+32+total > len(blob):
        raise ValueError('Invalid 64-bit Mach-O')
    pos, end = start + 32, start + 32 + total
    segments, sections = [], []
    for _ in range(count):
        if pos+8 > end:
            raise ValueError('Truncated load command')
        cmd, length = struct.unpack_from('<II', blob, pos)
        if length < 8 or pos+length > end:
            raise ValueError('Invalid load command length')
        if cmd == 0x19:
            if length < 72:
                raise ValueError('Truncated segment')
            name = blob[pos+8:pos+24].split(b'\0')[0].decode('ascii')
            vm, vmsize, offset, size = struct.unpack_from('<4Q', blob, pos+24)
            nsects = struct.unpack_from('<I', blob, pos+64)[0]
            if 72 + nsects*80 > length:
                raise ValueError('Truncated sections')
            segments.append(dict(name=name, vm=vm, offset=offset, size=size))
            for i in range(nsects):
                p = pos + 72 + i*80
                sect = blob[p:p+16].split(b'\0')[0].decode('ascii')
                seg = blob[p+16:p+32].split(b'\0')[0].decode('ascii')
                addr, size = struct.unpack_from('<QQ', blob, p+32)
                offset = struct.unpack_from('<I', blob, p+48)[0]
                sections.append(dict(segment=seg, name=sect, addr=addr, size=size, offset=offset))
        pos += length
    return filetype, segments, sections

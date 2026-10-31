"""Bounded installed IMG4/LZFSE kernel decoding using Apple's system library."""
import ctypes
import struct

MAX_INPUT = 64 * 1024 * 1024
MAX_OUTPUT = 128 * 1024 * 1024


def der_items(blob):
    pos = 0
    while pos < len(blob):
        if pos + 2 > len(blob):
            raise ValueError('Truncated DER header')
        tag, length = blob[pos:pos+2]
        pos += 2
        if length & 128:
            count = length & 127
            if not 1 <= count <= 4 or pos+count > len(blob) or blob[pos] == 0:
                raise ValueError('Invalid DER length')
            length = int.from_bytes(blob[pos:pos+count], 'big')
            pos += count
            if length < 128:
                raise ValueError('Noncanonical DER length')
        if pos+length > len(blob):
            raise ValueError('Truncated DER value')
        yield tag, blob[pos:pos+length]
        pos += length


def kernel_payload(blob):
    if not 1 <= len(blob) <= MAX_INPUT:
        raise ValueError('Kernel input exceeds bounds')
    outer = list(der_items(blob))
    if len(outer) != 1 or outer[0][0] != 0x30:
        raise ValueError('Expected one IMG4/IM4P sequence')
    nodes = list(der_items(outer[0][1]))
    if nodes and nodes[0] == (0x16, b'IMG4'):
        if len(nodes) < 2 or nodes[1][0] != 0x30:
            raise ValueError('Missing IM4P')
        nodes = list(der_items(nodes[1][1]))
    if (len(nodes) < 4 or nodes[0] != (0x16, b'IM4P') or
            nodes[1] != (0x16, b'krnl') or nodes[2][0] != 0x16 or nodes[3][0] != 4):
        raise ValueError('Not a kernel IM4P payload')
    return nodes[3][1]


def arm64_slice(blob):
    if blob[:4] == b'\xca\xfe\xba\xbe':
        if len(blob) < 8:
            raise ValueError('Truncated fat kernel')
        count = struct.unpack_from('>I', blob, 4)[0]
        if not 1 <= count <= 16 or 8+20*count > len(blob):
            raise ValueError('Invalid fat architecture table')
        selected = []
        for i in range(count):
            cpu, subtype, offset, size, alignment = struct.unpack_from('>5I', blob, 8+20*i)
            if offset < 8+20*count or offset+size > len(blob) or alignment > 31 or offset % (1 << alignment):
                raise ValueError('Invalid fat slice bounds/alignment')
            if cpu == 0x100000c:
                selected.append(blob[offset:offset+size])
        if len(selected) != 1:
            raise ValueError('No unique ARM64 kernel slice')
        blob = selected[0]
    if len(blob) < 32 or struct.unpack_from('<II', blob) != (0xfeedfacf, 0x100000c):
        raise ValueError('Not an ARM64 Mach-O kernel')
    return blob


def decode(blob):
    payload = kernel_payload(blob)
    if payload[:4] != b'bvx2':
        raise ValueError('Only installed LZFSE kernel payloads are qualified')
    lib = ctypes.CDLL('/usr/lib/libcompression.dylib')
    call = lib.compression_decode_buffer
    call.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                     ctypes.c_size_t, ctypes.c_void_p, ctypes.c_int]
    call.restype = ctypes.c_size_t
    source = ctypes.create_string_buffer(payload)
    target = ctypes.create_string_buffer(MAX_OUTPUT)
    size = call(target, MAX_OUTPUT, source, len(payload), None, 0x801)
    if not 0 < size < MAX_OUTPUT:
        raise ValueError('LZFSE decode failed or output exceeds bound')
    return arm64_slice(target.raw[:size])

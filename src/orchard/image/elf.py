"""Read ELF64 little-endian DT_NEEDED names without executing target binaries."""
import struct


def needed(data):
    if data[:6] != b'\x7fELF\x02\x01':
        return set()
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, count = struct.unpack_from('<HH', data, 54)
    segments, dynamic = [], None
    for index in range(count):
        entry = offset + index * size
        kind, flags, file_offset, virtual, _, file_size, _, _ = struct.unpack_from('<IIQQQQQQ', data, entry)
        if kind == 1:
            segments.append((virtual, file_offset, file_size))
        elif kind == 2:
            dynamic = (file_offset, file_size)
    if not dynamic:
        return set()
    names, strings = [], None
    for entry in range(dynamic[0], sum(dynamic), 16):
        tag, value = struct.unpack_from('<QQ', data, entry)
        if tag == 0:
            break
        if tag == 1:
            names.append(value)
        if tag == 5:
            strings = value
    if strings is None:
        return set()
    for virtual, file_offset, file_size in segments:
        if virtual <= strings < virtual + file_size:
            start = file_offset + strings - virtual
            return {data[start + name:data.index(b'\0', start + name)].decode('ascii') for name in names}
    raise ValueError('ELF string table is outside load segments')

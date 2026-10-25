"""Validate an Apple device tree and extract its unique chosen ECID."""
import struct


def validate(blob):
    if not 8 <= len(blob) <= 16 * 1024 * 1024:
        raise ValueError('ADT length outside expected bounds')
    position = 0
    budget = 100000
    device_ids = []

    def node(depth, parent=()):
        nonlocal position, budget
        budget -= 1
        if budget < 0 or depth > 64 or position + 8 > len(blob):
            raise ValueError('ADT node bounds')
        props, children = struct.unpack_from('<II', blob, position)
        position += 8
        if props > 4096 or children > 4096:
            raise ValueError('ADT node count')
        name = None
        unique_chip_id = None
        for _ in range(props):
            if position + 36 > len(blob):
                raise ValueError('ADT property header')
            label = blob[position:position + 32].split(b'\0', 1)[0]
            size = struct.unpack_from('<I', blob, position + 32)[0] & 0xffffff
            position += 36
            if position + ((size + 3) & ~3) > len(blob):
                raise ValueError('ADT property size')
            if label == b'name':
                name = blob[position:position + size].split(b'\0', 1)[0]
            elif label == b'unique-chip-id':
                unique_chip_id = blob[position:position + size]
            position += (size + 3) & ~3
        path = parent + (name,)
        if path == (b'device-tree', b'chosen'):
            if unique_chip_id is None or len(unique_chip_id) != 8:
                raise ValueError('Missing chosen device identity')
            device_ids.append(int.from_bytes(unique_chip_id, 'little'))
        for _ in range(children):
            node(depth + 1, path)
        return name

    if node(0) != b'device-tree':
        raise ValueError('Unexpected ADT root')
    if len(device_ids) != 1 or not device_ids[0]:
        raise ValueError('Ambiguous device identity in ADT')
    return device_ids[0]

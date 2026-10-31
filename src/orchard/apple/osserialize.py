"""Apple OSSerialize formats: legacy XML (ID/IDREF, without a plist wrapper) and binary."""
import base64
import struct
import xml.etree.ElementTree as ET


def apple_xml(blob):
    # Apple's legacy OSUnserialize XML uses ID/IDREF and no <plist> wrapper.
    ids = {}

    def visit(e):
        if 'IDREF' in e.attrib:
            return ids[e.attrib['IDREF']]
        text = e.text or ''
        if e.tag == 'dict':
            children = list(e)
            if len(children) % 2:
                raise ValueError('Unbalanced dictionary')
            value = {children[i].text: visit(children[i+1]) for i in range(0, len(children), 2)}
        elif e.tag == 'array':
            value = [visit(c) for c in e]
        elif e.tag in ('string', 'key'):
            value = text
        elif e.tag == 'integer':
            value = int(text, 16 if text.strip().startswith('0x') else 10)
        elif e.tag == 'data':
            value = base64.b64decode(text)
        elif e.tag in ('true', 'false'):
            value = e.tag == 'true'
        else:
            raise ValueError('Unexpected XML tag: ' + e.tag)
        if 'ID' in e.attrib:
            ids[e.attrib['ID']] = value
        return value

    return visit(ET.fromstring(blob.rstrip(b'\0')))


BINARY_MAGIC = b'\xd3\0\0\0'
_DICTIONARY, _ARRAY, _NUMBER, _SYMBOL, _STRING, _DATA, _BOOLEAN, _OBJECT = 1, 2, 4, 8, 9, 10, 11, 12


def binary(blob, max_size=16 << 20, max_depth=16, max_objects=4096):
    """OSSerializeBinary (kernel OSUnserializeBinary): dictionaries, arrays,
    64-bit numbers, symbols, strings, data, booleans and back-references.
    Bounded: rejects truncation, trailing bytes and oversized structures."""
    if blob[:4] != BINARY_MAGIC or len(blob) > max_size:
        raise ValueError('Not an OSSerializeBinary document')
    position, objects = 4, []

    def take(size):
        nonlocal position
        if size < 0 or size > len(blob) - position:
            raise ValueError('Truncated OSSerializeBinary document')
        result = blob[position:position + size]
        position += size
        return result

    def read(depth):
        if depth > max_depth or len(objects) > max_objects:
            raise ValueError('OSSerializeBinary document nests too deeply')
        header, = struct.unpack('<I', take(4))
        kind, size = (header >> 24) & 0x7f, header & 0xffffff
        if kind == _OBJECT:
            if size >= len(objects):
                raise ValueError('Invalid OSSerializeBinary back-reference')
            return objects[size]
        if kind in (_DICTIONARY, _ARRAY):
            value = {} if kind == _DICTIONARY else []
            objects.append(value)
            for _ in range(size):
                item = read(depth + 1)
                if kind == _DICTIONARY:
                    if not isinstance(item, str) or item in value:
                        raise ValueError('Invalid OSSerializeBinary dictionary key')
                    value[item] = read(depth + 1)
                else:
                    value.append(item)
            return value
        if kind in (_SYMBOL, _STRING, _DATA):
            value = take(size)
            take(-size % 4)
            if kind != _DATA:
                value = value.rstrip(b'\0').decode()
        elif kind == _NUMBER and size == 64:
            value, = struct.unpack('<Q', take(8))
        elif kind == _BOOLEAN and size in (0, 1):
            value = bool(size)
        else:
            raise ValueError(f'Unsupported OSSerializeBinary object {kind}/{size}')
        objects.append(value)
        return value

    value = read(0)
    if position != len(blob):
        raise ValueError('Trailing data after the OSSerializeBinary document')
    return value

"""Synthetic binary-format regression vectors; no firmware or device data."""
import struct
import unittest

from orchard_cli.apple.adt_tree import parse, u32s
from orchard_cli.apple.macho import macho
from orchard_cli.apple.osserialize import apple_xml
from orchard_cli.apple import z2fw as touch


def property_record(name, value):
    return name.encode().ljust(32, b'\0') + struct.pack('<I', len(value)) + value + b'\0' * (-len(value) % 4)


class BinaryCodecs(unittest.TestCase):
    def test_adt_tree_names_properties_and_children(self):
        child = struct.pack('<II', 1, 0) + property_record('name', b'child\0')
        root = (struct.pack('<II', 2, 1) + property_record('name', b'device-tree\0') +
                property_record('words', struct.pack('<II', 1, 0xffffffff)) + child)
        tree = parse(root)
        self.assertEqual([path for path, _ in tree.walk()], ['/device-tree', '/device-tree/child'])
        self.assertEqual(u32s(tree.properties['words']), [1, 0xffffffff])

    def test_adt_rejects_duplicate_truncated_and_excessive_properties(self):
        prop = property_record('name', b'root\0')
        for blob in (b'', struct.pack('<II', 4097, 0),
                     struct.pack('<II', 1, 0) + prop[:-1],
                     struct.pack('<II', 2, 0) + prop + prop):
            with self.subTest(blob=blob[:8]), self.assertRaises(ValueError):
                parse(blob)
        with self.assertRaises(ValueError):
            u32s(b'abc')

    def test_apple_xml_references_and_scalar_types(self):
        result = apple_xml(b'<array><data ID="7">AQID</data><data IDREF="7"/>'
                           b'<integer>0x20</integer><true/><false/><string>test</string></array>\0')
        self.assertEqual(result, [b'\x01\x02\x03', b'\x01\x02\x03', 32, True, False, 'test'])
        for blob in (b'<dict><key>unpaired</key></dict>', b'<unknown/>'):
            with self.assertRaises(ValueError):
                apple_xml(blob)

    def test_macho_segment_and_section_offsets(self):
        segment = struct.pack('<II16s4Q4I', 0x19, 152, b'__DATA', 0x1000, 0x100, 184, 4, 3, 3, 1, 0)
        section = struct.pack('<16s16sQQ8I', b'__firmware', b'__DATA', 0x1000, 4, 184, 0, 0, 0, 0, 0, 0, 0)
        header = struct.pack('<8I', 0xfeedfacf, 0, 0, 2, 1, 152, 0, 0)
        kind, segments, sections = macho(header + segment + section + b'data')
        self.assertEqual(kind, 2)
        self.assertEqual(segments, [{'name': '__DATA', 'vm': 0x1000, 'offset': 184, 'size': 4}])
        self.assertEqual(sections, [{'segment': '__DATA', 'name': '__firmware', 'addr': 0x1000,
                                     'size': 4, 'offset': 184}])
        for blob in (header[:-1], header + segment[:-1], b'\0' * 32):
            with self.assertRaises(ValueError):
                macho(blob)

    def test_touch_integer_bounds_and_word_encoding(self):
        self.assertEqual(touch.uint('42', 'value'), 42)
        for value in (True, -1, 0x100000000, '0x20', 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                touch.uint(value, 'value')
        self.assertEqual(touch.xe32(0x12345678), b'\x56\x78\x12\x34')
        for value in (0, 1, 0x12345678, 0xffffffff):
            self.assertEqual(touch.from_xe32(touch.xe32(value)), value)
        self.assertEqual(touch.swapped(b'\x01\x02\x03\x04'), b'\x02\x01\x04\x03')
        with self.assertRaises(ValueError):
            touch.swapped(b'odd')

    def test_blob_packets_decode_and_reject_damage(self):
        # HBPP blob packet as apple_z2 builds it for J71b calibration.
        packet = bytes.fromhex('18e1300100000000001000100201000300060000')
        self.assertEqual(touch.decode_blob(packet), (0x100000, b'\x01\x02\x03\0'))
        for index in (0, 4, 10, 12, len(packet) - 1):
            damaged = bytearray(packet)
            damaged[index] ^= 1
            with self.subTest(index=index), self.assertRaises(ValueError):
                touch.decode_blob(bytes(damaged))

    def test_calibration_records_have_exact_wire_layout(self):
        payload = struct.pack('<III', 0x100000, 0x500, touch.PROVIDERS['firefly-calibration'])
        record = touch.command(touch.CALIBRATION, payload)
        # code 6, size 12, then address, maximum size and provider, little-endian.
        self.assertEqual(record.hex(), '06000000' '0c000000' '00001000' '00050000' '05000000')
        container = struct.pack('<II', touch.MAGIC, 1) + record
        self.assertEqual(touch.decode_container(container), [(touch.CALIBRATION, payload)])

    def test_register_packets_have_exact_wire_layout(self):
        self.assertEqual(touch.register_packet(0x12345678, 0xffffffff, 1).hex(),
                         '1e3356781234ffffffff000100000511')

    def test_z2fw_commands_padding_and_bounds(self):
        header = struct.pack('<II', touch.MAGIC, 1)
        payload = touch.command(touch.SEND, b'abc')
        self.assertEqual(payload, b'\x01\0\0\0\x03\0\0\0abc\0')
        self.assertEqual(touch.decode_container(header + payload), [(touch.SEND, b'abc')])
        for blob in (b'', header + payload[:-1], header + payload[:-1] + b'x',
                     header + struct.pack('<II', 99, 0), header + b'x'):
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                touch.decode_container(blob)


if __name__ == '__main__':
    unittest.main()

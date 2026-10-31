"""Firmware recipe steps: vocabulary check, hash verification and containers."""
import hashlib
import struct
import unittest

from orchard import recipe


def sha(data):
    return hashlib.sha256(data).hexdigest()


def der(tag, value):
    length = len(value)
    if length < 0x80:
        return bytes([tag, length]) + value
    size = (length.bit_length() + 7) // 8
    return bytes([tag, 0x80 | size]) + length.to_bytes(size, 'big') + value


def sample(steps, data=b'firmware'):
    return {
        'schema': 2, 'device': 'iPad7,5',
        'ipsw': {'version': '17.7.11', 'build': '21H461', 'bytes': 1, 'sha256': '0' * 64,
                 'url': 'https://updates.cdn-apple.com/x.ipsw'},
        'inputs': {'blob': {'ipsw': {'rootfs': 'usr/share/firmware/blob'}, 'sha256': sha(data)}},
        'outputs': [{'destination': 'lib/firmware/apple/blob', 'sha256': sha(data), 'steps': steps}],
    }


class RecipeTests(unittest.TestCase):
    def test_unknown_step_asks_for_an_update_before_any_work(self):
        with self.assertRaisesRegex(recipe.UnsupportedRecipe, 'update Orchard'):
            recipe.check(sample([{'step': 'future-format@1', 'input': 'blob'}]))

    def test_wrong_parameters_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'Wrong parameters'):
            recipe.check(sample([{'step': 'copy@1', 'input': 'blob', 'profile': 'x'}]))

    def test_smartio_kext_is_named_and_checked(self):
        recipe.check(sample([{'step': 'smartio-from-kernelcache@2', 'input': 'blob',
                              'kext': 'com.apple.driver.AppleS8000SmartIO'}]))
        with self.assertRaisesRegex(ValueError, 'Wrong parameters'):
            recipe.check(sample([{'step': 'smartio-from-kernelcache@2', 'input': 'blob'}]))
        document = recipe.check(sample([{'step': 'smartio-from-kernelcache@2', 'input': 'blob',
                                         'kext': 'com.apple.driver.AppleX9999SmartIO'}]))
        with self.assertRaisesRegex(ValueError, 'Unknown SmartIO kext'):
            recipe.run(document, lambda name: b'firmware')

    def test_z2_packet_is_validated_and_returned_unmodified(self):
        import plistlib
        from orchard.apple import z2packet
        payload = bytes(range(8))
        words = len(payload) // 4
        header = words.to_bytes(2, 'big') + bytes(4)
        checksum = sum(payload) & 0xffffffff
        tail = checksum.to_bytes(4, 'little')
        tail = bytes((tail[1], tail[0], tail[3], tail[2]))
        packet = z2packet.PREFIX + header + sum(header).to_bytes(2, 'big') + payload + tail
        profile = plistlib.dumps({'C1F15,1': {'PreconstructedBootloadPacketType': 'Z2',
                                              'Constructed Firmware': packet}})
        self.assertEqual(z2packet.extract(profile, 'C1F15,1'), packet)
        corrupt = plistlib.dumps({'C1F15,1': {'PreconstructedBootloadPacketType': 'Z2',
                                              'Constructed Firmware': packet[:-5] + b'\xff' + packet[-4:]}})
        with self.assertRaisesRegex(ValueError, 'checksum'):
            z2packet.extract(corrupt, 'C1F15,1')
        with self.assertRaisesRegex(ValueError, 'No preconstructed'):
            z2packet.extract(profile, 'K1F19,4')
        from orchard.apple import z2fw
        records = z2fw.decode_container(z2packet.convert(profile, 'C1F15,1', 0x10009000, 0x400))
        self.assertEqual([code for code, _ in records], [z2fw.CONFIG, z2fw.CALIBRATION, z2fw.SEND])
        self.assertEqual(records[2][1], packet)
        recipe.check(sample([{'step': 'z2fw-from-z2-packet@1', 'input': 'blob', 'profile': 'C1F15,1',
                              'calibration_address': 0x10009000, 'calibration_size': 0x400}]))

    def test_vxd_firmware_step_is_known(self):
        recipe.check(sample([{'step': 'vxd-firmware-from-kernelcache@1', 'input': 'blob'}]))

    def test_inputs_and_outputs_are_hash_checked(self):
        document = recipe.check(sample([{'step': 'copy@1', 'input': 'blob'}]))
        self.assertEqual(recipe.run(document, lambda name: b'firmware'),
                         {'lib/firmware/apple/blob': b'firmware'})
        with self.assertRaisesRegex(ValueError, 'input differs'):
            recipe.run(document, lambda name: b'tampered')
        document['inputs']['blob'].pop('sha256')
        document['outputs'][0]['sha256'] = '1' * 64
        with self.assertRaisesRegex(ValueError, 'output differs'):
            recipe.run(document, lambda name: b'firmware')

    def test_needed_inputs_are_only_those_of_the_requested_outputs(self):
        document = recipe.check(sample([{'step': 'copy@1', 'input': 'blob'}]))
        self.assertEqual(recipe.needed_inputs(document), ['blob'])
        self.assertEqual(recipe.needed_inputs(document, []), [])

    def test_later_steps_take_the_previous_result(self):
        container = der(0x30, der(0x16, b'IM4P') + der(0x16, b'mtfw') + der(0x16, b'touch')
                        + der(0x04, b'firmware'))
        document = sample([{'step': 'im4p-payload@1', 'input': 'blob'}, {'step': 'copy@1'}], container)
        document['outputs'][0]['sha256'] = sha(b'firmware')
        recipe.check(document)
        self.assertEqual(recipe.run(document, lambda name: container),
                         {'lib/firmware/apple/blob': b'firmware'})
        document['outputs'][0]['steps'][1]['input'] = 'blob'
        with self.assertRaisesRegex(ValueError, 'Only the first step'):
            recipe.supported(document)

    def test_im4p_payload(self):
        payload = b'\x7fELF' + bytes(300)
        container = der(0x30, der(0x16, b'IM4P') + der(0x16, b'mtfw') + der(0x16, b'touch')
                        + der(0x04, payload))
        self.assertEqual(recipe.im4p_payload(container), payload)
        compressed = der(0x30, der(0x16, b'IM4P') + der(0x16, b'mtfw') + der(0x16, b'touch')
                         + der(0x04, b'bvx2' + bytes(8)))
        with self.assertRaisesRegex(ValueError, 'Compressed'):
            recipe.im4p_payload(compressed)
        with self.assertRaisesRegex(ValueError, 'Not an IM4P'):
            recipe.im4p_payload(der(0x30, der(0x16, b'IMG4') + der(0x16, b'a') + der(0x16, b'b') + der(0x04, b'c')))


    def test_aop_image_picks_one_coprocessor_image(self):
        def obj(kind, size, payload=b''):
            return struct.pack('<I', kind << 24 | size) + payload + bytes(-len(payload) % 4)

        def string(text):
            data = text.encode() + b'\0'
            return obj(9, len(data), data)

        def entry(role, data):
            return (obj(1, 2) + string('header') + obj(1, 1) + string('coprocessor') + string(role)
                    + string('data') + obj(10, len(data), data))

        container = (b'\xd3\0\0\0' + obj(1, 1) + string('firmware') + obj(2, 2)
                     + entry('AOP', b'main') + entry('AOP_ext', b'extension'))
        self.assertEqual(recipe.aop_image(container, 'AOP'), b'main')
        self.assertEqual(recipe.aop_image(container, 'AOP_ext'), b'extension')
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            recipe.aop_image(container, 'SEP')
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            recipe.aop_image(container[:-3], 'AOP')
        with self.assertRaisesRegex(ValueError, 'Trailing'):
            recipe.aop_image(container + bytes(4), 'AOP')
        with self.assertRaisesRegex(ValueError, 'Not an OSSerializeBinary'):
            recipe.aop_image(b'<dict/>', 'AOP')

if __name__ == '__main__':
    unittest.main()

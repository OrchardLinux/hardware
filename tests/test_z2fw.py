"""Touch firmware conversion of makez2fw's input forms, on synthetic fixtures (no Apple firmware).

The pinned hashes of makez2fw-compatible outputs were produced by Paul
Praschl's makez2fw (944541e67b4c603df328c77d30093551de802c17), built from
source, on the same fixtures written to files.
"""
import base64
import hashlib
import plistlib
import struct
import unittest

from orchard import recipe
from orchard.apple import z2fw
from orchard.apple.osserialize import apple_xml


def sha(data):
    return hashlib.sha256(data).hexdigest()


def b64(data):
    return base64.b64encode(data).decode()


def mtfw_fixture(calibration=True):
    """MTFW-style OSSerialize XML: one personality of HBPP items, with ID/IDREF reuse."""
    prop = ('<dict><key>SysConfigKey</key><string>MtCl</string><key>Payload</key><data>AAAAAAAAAAA=</data>'
            '<key>Property</key><string>multi-touch-calibration</string><key>MaxSize</key>'
            '<integer size="64">0x600</integer><key>Address</key><integer size="64">0x100400</integer>'
            '<key>Type</key><string>Property</string></dict>') if calibration else ''
    return (
        '<dict><key>TEST,6</key><array>'
        '<dict><key>Address</key><integer ID="0" size="64">0x400b0000</integer><key>Mask</key>'
        '<integer ID="1" size="64">0x1</integer><key>Type</key><string ID="2">ReadModifyWrite</string>'
        '<key>Value</key><integer ID="3" size="32">0x0</integer><key>SkipCheck</key><false/></dict>'
        '<dict><key>Address</key><integer size="64">0x100000</integer><key>Payload</key>'
        f'<data>{b64(bytes(range(1, 11)))}</data><key>Type</key><string>Binary</string></dict>'
        + prop +
        '<dict><key>Address</key><integer IDREF="0"/><key>Type</key><string IDREF="2"/>'
        '<key>Value</key><integer size="32">0x1</integer></dict>'
        '<dict><key>Type</key><string>Config</string><key>Config</key><dict>'
        '<key>Protocol</key><string>Z2</string><key>Z2 Config</key><dict><key>Inter Packet Delay</key>'
        '<integer size="32">125</integer></dict><key>Min DMA Transfer Size</key><integer size="32">17</integer>'
        '<key>SPI Config</key><dict><key>Clock Phase</key><integer size="32">1</integer><key>Word Delay</key>'
        '<integer size="32">0</integer><key>Clock Polarity</key><integer size="32">1</integer>'
        '<key>CS Delay</key><integer size="32">10000</integer><key>Clock Period Ms</key>'
        '<integer size="32">41</integer></dict><key>Power Off On Reset</key><true/>'
        '<key>Device Tree Overrides</key><dict>'
        f'<key>otp-address</key><data>{b64(struct.pack("<I", 0x400b6000))}</data>'
        f'<key>hbpp-version</key><data>{b64(struct.pack("<I", 0x104))}</data>'
        f'<key>otp-sn</key><data>{b64(struct.pack("<II", 62, 4))}</data></dict>'
        '<key>Boot Timeout</key><dict><key>Normal Boot Ms</key><integer size="32">500</integer></dict>'
        '</dict></dict></array></dict>').encode() + b'\0'


def mtprops_fixture(boot_timeout=True):
    """mtprops with an array of preconstructed packets and an OSSerialize firmware config."""
    timeout = ('<key>Boot Timeout</key><dict><key>Normal Boot Ms</key><integer>300</integer></dict>'
               if boot_timeout else '')
    config = (
        '<dict><key>Protocol</key><string>Z2</string><key>Min DMA Transfer Size</key><integer>8</integer>'
        '<key>Power Off On Reset</key><false/><key>Z2 Config</key><dict><key>Inter Packet Delay</key>'
        '<integer>50</integer></dict><key>SPI Config</key><dict><key>Clock Phase</key><integer>0</integer>'
        '<key>Clock Polarity</key><integer>0</integer><key>Word Delay</key><integer>0</integer>'
        '<key>CS Delay</key><integer>45</integer><key>Clock Period Ms</key><integer>62</integer></dict>'
        + timeout +
        '<key>Calibration Sequence</key><array>'
        '<dict><key>Provider</key><string>multi-touch-calibration</string><key>Address</key>'
        '<integer>0x10009000</integer><key>Max Size</key><integer>1800</integer></dict>'
        '<dict><key>Provider</key><string>orb-gap-cal</string><key>Address</key>'
        '<integer>0x1000a000</integer><key>Max Size</key><integer>992</integer></dict></array>'
        '<key>Boot Sequence</key><array>'
        '<dict><key>Address</key><integer>0x10003060</integer><key>Mask</key><integer>0xffffffff</integer>'
        '<key>Value</key><integer>6099</integer></dict>'
        '<dict><key>Action</key><string>RequestCalibration</string></dict>'
        '<dict><key>Address</key><integer>0x10003000</integer><key>Value</key><integer>3</integer></dict>'
        '</array></dict>').encode() + b'\0'
    return plistlib.dumps({'TEST,2': {
        'PreconstructedBootloadPacketType': 'TESTPKT',
        'Constructed Firmware': [b'\x18\xe1\x30\x01' + bytes(range(16)), b'\x1e\x33' + bytes(14)],
        'Firmware Config': config}})


def gen1_fixture():
    """Legacy data-form mtprops; eight synthetic payload bytes, full word count."""
    return {'PreconstructedBootloadPacketType': 'Z2', 'Constructed Firmware':
            bytes.fromhex('18e13001' '0002' '00000000' '0002' '0100030205040706' '001c0000')}


def convert_packets(data, packet='TESTPKT'):
    profile = plistlib.loads(data)['TEST,2']
    return z2fw.convert_packets(profile, apple_xml(profile['Firmware Config']), packet=packet)


class PacketEncoding(unittest.TestCase):
    def test_hbpp14_blob_matches_the_driver_layout_and_roundtrips(self):
        packet = z2fw.encode_blob(0x100000, b'\x01\x02\x03')
        self.assertEqual(packet.hex(), '18e1300100000000001000100201000300060000')
        self.assertEqual(z2fw.decode_blob(packet), (0x100000, b'\x01\x02\x03\0'))
        data = bytes(range(256)) * 3
        self.assertEqual(z2fw.decode_blob(z2fw.encode_blob(0x12345678, data)), (0x12345678, data))
        for address, data in ((0, b''), (0xfffffffe, b'abcd'), (0, bytes(0x40001))):
            with self.subTest(address=address, size=len(data)), self.assertRaises(ValueError):
                z2fw.encode_blob(address, data)

    def test_register_read_is_two_bit_reversed_raw_transfers(self):
        (c1, request), (c2, exchange) = z2fw.register_read(0x400b6020)
        self.assertEqual((c1, c2), (z2fw.RAW_XFER, z2fw.RAW_XFER))
        # tx 8, rx 0, flags 1; 1c73, address word-swapped, BE16 sum of the address bytes.
        self.assertEqual(request.hex(), '08000000' '00000000' '01000000' '1c73' '6020400b' '00cb')
        self.assertEqual(exchange.hex(), '08000000' '08000000' '01000000' '1aa118e118e118e1')
        with self.assertRaises(ValueError):
            z2fw.raw_xfer(bytes(17), 0)

    def test_otp_preflight_reads_each_selected_word(self):
        records = z2fw.otp_preflight_records(0x400b6000, 62, 8)
        self.assertEqual(len(records), 3 + 2 * 9)
        self.assertEqual(records[3], (z2fw.SEND, z2fw.register_packet(0x400b6010, 0xffffffff, 0xffffffff)))
        self.assertEqual(records[4], (z2fw.SEND, z2fw.register_packet(0x400b600c, 0xffffffff, 62)))
        self.assertEqual(records[13], (z2fw.SEND, z2fw.register_packet(0x400b600c, 0xffffffff, 63)))
        for size in (0, 6, 260):
            with self.subTest(size=size), self.assertRaises(ValueError):
                z2fw.otp_preflight_records(0x400b6000, 0, size)

    def test_config_valid_mask_follows_present_fields(self):
        config = {'Min DMA Transfer Size': 17, 'Power Off On Reset': True,
                  'Device Tree Overrides': {'hbpp-version': struct.pack('<I', 0x104), 'otp-sn': b'short'}}
        valid, words, wait, otp = z2fw.hbpp_config(config, overrides=True)
        self.assertEqual((valid, wait, otp), (1 | 1 << 7 | 1 << 11, z2fw.DEFAULT_WAIT_MS, None))
        self.assertEqual(struct.unpack('<11I', words), (valid, 17, 0, 0, 0, 0, 0, 0, 1, 0, 0x104))
        valid, words, _, _ = z2fw.hbpp_config(config, overrides=False)
        self.assertEqual(struct.unpack('<11I', words)[0], 1 | 1 << 7)
        self.assertEqual(z2fw.hbpp_config({}, overrides=True), (0, None, z2fw.DEFAULT_WAIT_MS, None))
        with self.assertRaises(ValueError):
            z2fw.hbpp_config({'Power Off On Reset': 1}, overrides=False)


class MtfwConversion(unittest.TestCase):
    def test_matches_makez2fw_without_calibration(self):
        output, info = z2fw.convert_mtfw(apple_xml(mtfw_fixture(False)), 'TEST,6', otp_preflight=True)
        self.assertEqual(sha(output), '23f44fec2e6e8c49f77567cc4dc6b7f1dee1666dc2fb0c35df6e61f371d62d87')
        self.assertTrue(info['otp_preflight'])

    def test_property_loads_the_default_then_a_calibration_record_in_place(self):
        root = apple_xml(mtfw_fixture())
        output, info = z2fw.convert_mtfw(root, 'TEST,6', otp_preflight=True)
        self.assertEqual(sha(output), '4edff904c3206be551f7d762069cb19ae388fb9f757fdfa386153d890fb4de7e')
        records = z2fw.decode_container(output)
        commands = [code for code, _ in records]
        self.assertEqual(commands[:2], [z2fw.CONFIG, z2fw.INIT])
        self.assertEqual(commands[-5:], [z2fw.SEND, z2fw.SEND, z2fw.CALIBRATION, z2fw.SEND, z2fw.WAIT])
        # Apple's default stays loaded on a unit whose ADT lacks the property.
        self.assertEqual(z2fw.decode_blob(records[-4][1]), (0x100400, bytes(8)))
        self.assertEqual(records[-3][1], struct.pack('<III', 0x100400, 0x600, 0))
        self.assertEqual(records[-1][1], struct.pack('<I', 500))
        self.assertEqual(struct.unpack('<11I', records[0][1]),
                         (0x1bff, 17, 125, 10000, 1, 1, 0, 41, 1, 500, 0x104))
        self.assertEqual(z2fw.decode_blob(records[-5][1]), (0x100000, bytes(range(1, 11)) + b'\0\0'))
        # A Property item adds only its default and its record (makez2fw loads just the default).
        plain = z2fw.decode_container(z2fw.convert_mtfw(apple_xml(mtfw_fixture(False)), 'TEST,6',
                                                        otp_preflight=True)[0])
        self.assertEqual(records[:-4] + records[-2:], plain)
        without_otp = z2fw.decode_container(z2fw.convert_mtfw(root, 'TEST,6', otp_preflight=False)[0])
        self.assertEqual(without_otp, records[:2] + records[2 + 3 + 9:])

    def test_ipad7_providers(self):
        for name, provider in (('firefly-p100-calibration', 7), ('ext-multi-touch-calibration', 8)):
            root = apple_xml(mtfw_fixture())
            root['TEST,6'][2]['Property'] = name
            records = z2fw.decode_container(z2fw.convert_mtfw(root, 'TEST,6', otp_preflight=True)[0])
            self.assertEqual(records[-3][1], struct.pack('<III', 0x100400, 0x600, provider))

    def test_refuses_unknown_providers_per_unit_binaries_and_missing_profiles(self):
        root = apple_xml(mtfw_fixture())
        root['TEST,6'][2]['Property'] = 'unknown-calibration'
        with self.assertRaisesRegex(ValueError, 'No apple_z2 calibration provider'):
            z2fw.convert_mtfw(root, 'TEST,6', otp_preflight=True)
        root = apple_xml(mtfw_fixture())
        root['TEST,6'][1]['SysConfigKey'] = 'MtCl'
        with self.assertRaisesRegex(ValueError, 'per-unit'):
            z2fw.convert_mtfw(root, 'TEST,6', otp_preflight=True)
        with self.assertRaisesRegex(ValueError, 'personality not found'):
            z2fw.convert_mtfw(root, 'TEST,7', otp_preflight=True)
        root = apple_xml(mtfw_fixture())
        root['TEST,6'][1]['Type'] = 'Unknown'
        with self.assertRaisesRegex(ValueError, 'Unsupported HBPP item'):
            z2fw.convert_mtfw(root, 'TEST,6', otp_preflight=True)


class PacketArrayConversion(unittest.TestCase):
    def test_matches_makez2fw_dynamic_calibration(self):
        output, info = convert_packets(mtprops_fixture())
        self.assertEqual(sha(output), '07cb964deb2091684f04e242c8bce576d7e7aabbed8799300553b69ae92b1c91')
        self.assertEqual(info['command_sequence'], [0, 1, 1, 4, 6, 6, 1, 1, 1, 3])
        records = z2fw.decode_container(output)
        self.assertEqual(records[4][1], struct.pack('<III', 0x10009000, 1800, 0))
        self.assertEqual(records[5][1], struct.pack('<III', 0x1000a000, 992, 1))
        self.assertEqual(records[7], (z2fw.SEND, z2fw.REQUEST_CALIBRATION))
        self.assertEqual(records[-1][1], struct.pack('<I', 300))

    def test_wait_defaults_without_boot_timeout(self):
        output, info = convert_packets(mtprops_fixture(boot_timeout=False))
        self.assertEqual(sha(output), '95dc5d72007468b49c811bae7fbbb7f68c2b6dcfbcf6263b1e6b40147c157177')
        self.assertEqual(info['wait_ms'], z2fw.DEFAULT_WAIT_MS)

    def test_refuses_other_packet_types_and_data_form(self):
        with self.assertRaisesRegex(ValueError, 'packet type'):
            convert_packets(mtprops_fixture(), packet='OTHER')
        profile = plistlib.loads(mtprops_fixture())['TEST,2']
        profile['Constructed Firmware'] = b'\x18\xe1'
        with self.assertRaisesRegex(ValueError, 'array-form'):
            z2fw.convert_packets(profile, apple_xml(profile['Firmware Config']), packet='TESTPKT')


class Gen1Conversion(unittest.TestCase):
    def convert(self, profile=None, **kwargs):
        return z2fw.convert_gen1(gen1_fixture() if profile is None else profile,
                                 **({'packet': 'Z2', 'touch_maximum': 1024, 'prox_maximum': 128,
                                     'fll_mval': 6099, 'ref_clk_div': 2, 'clk32_clock_enable': 1} | kwargs))

    def test_corellium_gen1_sequence_preserves_wire_bytes_and_dynamic_calibration(self):
        output, info = self.convert()
        exchange = bytes.fromhex('1aa118e118e118e118e118e118e118e1')
        records = z2fw.decode_container(output)
        self.assertEqual(records, [
            (z2fw.INIT, bytes.fromhex('19c1')),
            (z2fw.INIT, exchange), (z2fw.INIT, exchange), (z2fw.INIT, exchange),
            (z2fw.CALIBRATION, struct.pack('<III', 0x10009600, 128, 9)),
            (z2fw.CALIBRATION, struct.pack('<III', 0x10009000, 1024, 0)),
            (z2fw.SEND, gen1_fixture()['Constructed Firmware']),
            (z2fw.SEND, bytes.fromhex('1e3330601000ffffffff17d300000586')),
            (z2fw.SEND, bytes.fromhex('1e33305c1000ffffffff00020000049a')),
            (z2fw.SEND, bytes.fromhex('1e3330581000ffffffff00060000049a')),
            (z2fw.SEND, bytes.fromhex('1e3330001000ffffffff00030000043f')),
            (z2fw.SEND, bytes.fromhex('1e3335181000ffffffff00010000045a')),
            (z2fw.SEND, bytes.fromhex('1f01')),
            (z2fw.INIT, bytes.fromhex('1d5334001000000100000045')),
        ])
        self.assertEqual(info['command_sequence'], [0, 0, 0, 0, 6, 6, 1, 1, 1, 1, 1, 1, 1, 0])
        self.assertEqual((info['firmware_address'], info['firmware_payload_bytes']), (0, 8))
        self.assertEqual(z2fw.decode_blob(records[6][1], hbpp_version=0x103), (0, bytes(range(8))))
        # A legacy full word count must fail the HBPP14 length check.
        with self.assertRaisesRegex(ValueError, 'length mismatch'):
            z2fw.decode_blob(records[6][1])

    def test_board_clock_policy_preserves_zero_overrides(self):
        records = z2fw.decode_container(self.convert(ref_clk_div=0, clk32_clock_enable=0)[0])
        self.assertEqual(records[8], (z2fw.SEND, bytes.fromhex('1e33305c1000ffffffff000000000498')))
        self.assertEqual(records[11], (z2fw.SEND, bytes.fromhex('1e3335181000ffffffff000000000459')))

    def test_rejects_wrong_input_forms_bad_packets_and_overlapping_calibration(self):
        for profile, message in (
                (gen1_fixture() | {'Constructed Firmware': [b'packet']}, 'data-form Gen1'),
                (gen1_fixture() | {'Firmware Config': b'config'}, 'unsupported config'),
                (gen1_fixture() | {'Constructed Firmware': gen1_fixture()['Constructed Firmware'][:-1]},
                 'length mismatch'),
                (gen1_fixture() | {'Constructed Firmware': gen1_fixture()['Constructed Firmware'][:-1] + b'\1'},
                 'payload checksum')):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.convert(profile)
        for kwargs, message in (({'packet': 'K1HBPP14'}, 'packet type'),
                                ({'touch_maximum': 0x604}, 'Overlapping'),
                                ({'prox_maximum': 0}, 'Empty calibration'),
                                ({'touch_maximum': True}, 'Boolean'),
                                ({'fll_mval': -1}, 'out of bounds'),
                                ({'ref_clk_div': True}, 'Boolean'),
                                ({'clk32_clock_enable': 0x100000000}, 'out of bounds')):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ValueError, message):
                self.convert(**kwargs)


class RecipeSteps(unittest.TestCase):
    def document(self, steps, data, output):
        return {
            'schema': 2, 'device': 'iPad7,12',
            'ipsw': {'version': '18.7.11', 'build': '22H384', 'bytes': 1, 'sha256': '0' * 64,
                     'url': 'https://updates.cdn-apple.com/x.ipsw'},
            'inputs': {'touch': {'ipsw': {'zip': 'Firmware/touch.im4p'}, 'sha256': sha(data)}},
            'outputs': [{'destination': 'lib/firmware/apple/touch.z2fw', 'sha256': sha(output), 'steps': steps}],
        }

    def test_mtfw_from_an_im4p_through_two_steps(self):
        payload = mtfw_fixture()
        output, _ = z2fw.convert_mtfw(apple_xml(payload), 'TEST,6', otp_preflight=True)
        im4p = der(0x30, der(0x16, b'IM4P') + der(0x16, b'mtfw') + der(0x16, b'touch') + der(0x04, payload))
        steps = [{'step': 'im4p-payload@1', 'input': 'touch'},
                 {'step': 'z2fw-from-mtfw@1', 'profile': 'TEST,6', 'otp_preflight': True}]
        document = recipe.supported(self.document(steps, im4p, output))
        self.assertEqual(recipe.run(document, lambda name: im4p), {'lib/firmware/apple/touch.z2fw': output})

    def test_mtprops_packet_array_step(self):
        data = mtprops_fixture()
        output, _ = convert_packets(data)
        steps = [{'step': 'z2fw-from-mtprops@2', 'input': 'touch', 'profile': 'TEST,2', 'packet': 'TESTPKT'}]
        document = recipe.supported(self.document(steps, data, output))
        self.assertEqual(recipe.run(document, lambda name: data), {'lib/firmware/apple/touch.z2fw': output})

    def test_mtprops_gen1_step_uses_board_calibration_bounds(self):
        profile = gen1_fixture()
        data = plistlib.dumps({'TEST,1': profile})
        output, _ = z2fw.convert_gen1(profile, packet='Z2', touch_maximum=1024, prox_maximum=128,
                                     fll_mval=6099, ref_clk_div=0, clk32_clock_enable=0)
        steps = [{'step': 'z2fw-from-mtprops@3', 'input': 'touch', 'profile': 'TEST,1', 'packet': 'Z2',
                  'touch_maximum': 1024, 'prox_maximum': 128,
                  'fll_mval': 6099, 'ref_clk_div': 0, 'clk32_clock_enable': 0}]
        document = recipe.check(self.document(steps, data, output))
        self.assertEqual(recipe.run(document, lambda name: data), {'lib/firmware/apple/touch.z2fw': output})
        for field in ('touch_maximum', 'prox_maximum', 'fll_mval', 'ref_clk_div', 'clk32_clock_enable'):
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'must be int'):
                recipe.supported(self.document([steps[0] | {field: True}], data, output))

    def test_parameter_types_are_checked(self):
        for step in ({'step': 'z2fw-from-mtfw@1', 'input': 'touch', 'profile': 'TEST,6', 'otp_preflight': 1},
                     {'step': 'z2fw-from-mtfw@1', 'input': 'touch', 'profile': 6, 'otp_preflight': True},
                     {'step': 'z2fw-from-mtprops@1', 'input': 'touch', 'profile': 'K1F19,4', 'packet': 'K1HBPP14',
                      'load_address': True, 'hbpp_version': 0x104}):
            with self.subTest(step=step), self.assertRaisesRegex(ValueError, 'must be'):
                recipe.supported(self.document([step], b'', b''))
        with self.assertRaisesRegex(ValueError, 'Wrong parameters'):
            recipe.supported(self.document([{'step': 'z2fw-from-mtprops@2', 'input': 'touch', 'profile': 'x'}],
                                           b'', b''))


def der(tag, value):
    length = len(value)
    if length < 0x80:
        return bytes([tag, length]) + value
    size = (length.bit_length() + 7) // 8
    return bytes([tag, 0x80 | size]) + length.to_bytes(size, 'big') + value


if __name__ == '__main__':
    unittest.main()

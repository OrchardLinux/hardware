#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Convert Apple multitouch firmware (mtprops, MTFW) to Z2FW with calibration records.

The file is the same for every unit of a model: apple_z2 fills each
calibration record from the DT properties m1n1 copies from the ADT at boot.

Input forms:
- convert(): an mtprops profile whose "Constructed Firmware" is one
  preconstructed HBPP14 packet (J71b).
- convert_packets(): an mtprops profile whose "Constructed Firmware" is an
  array of preconstructed packets (iPhone 7 Plus); makez2fw's Gen2 path.
- convert_mtfw(): an MTFW image (IM4P payload) listing HBPP load items per
  personality (iPad 7); makez2fw's HBPP item path.
- convert_gen1(): a legacy Z2 mtprops data packet (iPhone 6), with the
  Gen1 bootstrap from Corellium Project Sandcastle.

convert_packets() and convert_mtfw() are ports of Paul Praschl's (Pauli1Go)
GPL-2.0-or-later makez2fw (mtfw.c, makez2fw.c), which builds on Corellium's
Project Sandcastle. They keep its command order, packets and configuration
words. Per-unit calibration is the one difference: where makez2fw loads
SysCfg data (or the MTFW's placeholder payload) as HBPP blob packets, these
emit calibration records in the same place. For mtprops packet arrays that
is exactly makez2fw --dynamic-calibration. makez2fw reads an MTFW's config
from the first config block in the file; convert_mtfw() reads the selected
personality's own config item.

Packet layouts/Z2FW framing informed by Corellium Project Sandcastle and
Pauli1Go's GPL makez2fw (944541e67b4c603df328c77d30093551de802c17).
"""
import struct


MAGIC = 0x5746325a
INIT, SEND, WAIT, CONFIG, RAW_XFER, CALIBRATION = 0, 1, 3, 4, 5, 6
# apple_z2 calibration provider IDs (enum apple_z2_calibration_provider in
# drivers/input/touchscreen/apple_z2.c), keyed by the ADT property name.
PROVIDERS = {'multi-touch-calibration': 0, 'orb-gap-cal': 1, 'orb-force-cal': 2,
             'shape-dynamic-accel-cal': 3, 'multitouch-to-display-offset': 4,
             'firefly-calibration': 5, 'pressure-effect-correction': 6,
             'firefly-p100-calibration': 7, 'ext-multi-touch-calibration': 8,
             'prox-calibration': 9}


def uint(value, name, maximum=0xffffffff):
    if isinstance(value, bool):
        raise ValueError('Boolean instead of integer: ' + name)
    if isinstance(value, str):
        if not value.isdecimal():
            raise ValueError('Nondecimal integer: ' + name)
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= maximum:
        raise ValueError('Integer out of bounds: ' + name)
    return value


def swapped(data):
    if len(data) % 2:
        raise ValueError('Word-swap requires even length')
    result = bytearray(data)
    result[0::2], result[1::2] = data[1::2], data[0::2]
    return bytes(result)


def xe32(value):
    return struct.pack('>HH', value & 0xffff, value >> 16)


def from_xe32(data):
    lo, hi = struct.unpack('>HH', data)
    return lo | hi << 16


def decode_blob(packet, *, hbpp_version=0x104):
    if len(packet) < 20 or packet[:4] != b'\x18\xe1\x30\x01':
        raise ValueError('Not a prefixed HBPP blob')
    words = struct.unpack_from('>H', packet, 4)[0]
    size = (words + (hbpp_version >= 0x104)) * 4
    if len(packet) != 16 + size:
        raise ValueError('HBPP length mismatch or extra trailing packets')
    if sum(packet[4:10]) != struct.unpack_from('>H', packet, 10)[0]:
        raise ValueError('HBPP header checksum mismatch')
    payload = swapped(packet[12:-4])
    if sum(payload) & 0xffffffff != from_xe32(packet[-4:]):
        raise ValueError('HBPP payload checksum mismatch')
    address = from_xe32(packet[6:10])
    if address + size > 0x100000000:
        raise ValueError('HBPP address range overflow')
    return address, payload


def register_packet(address, mask, value):
    fields = xe32(uint(address, 'register address')) + xe32(uint(mask, 'register mask'))
    fields += xe32(uint(value, 'register value'))
    return b'\x1e\x33' + fields + struct.pack('>H', sum(fields))


def command(code, payload):
    return struct.pack('<II', code, len(payload)) + payload + b'\0' * (-len(payload) % 4)


def decode_container(blob):
    if len(blob) < 8 or struct.unpack_from('<II', blob) != (MAGIC, 1):
        raise ValueError('Invalid Z2FW header')
    pos = 8
    records = []
    while pos < len(blob):
        if pos+8 > len(blob):
            raise ValueError('Truncated Z2FW command')
        code, size = struct.unpack_from('<II', blob, pos)
        pos += 8
        next_pos = (pos+size+3)&~3
        if code not in (INIT, SEND, WAIT, CONFIG, RAW_XFER, CALIBRATION) or next_pos > len(blob):
            raise ValueError('Unknown or truncated Z2FW command')
        if any(blob[pos+size:next_pos]):
            raise ValueError('Nonzero Z2FW padding')
        records.append((code, blob[pos:pos+size]))
        pos = next_pos
    return records


def calibration_record(name, address, maximum, providers):
    """Z2FW command 6: apple_z2 sends the DT property of this provider to address.

    providers collects the records so far and rejects duplicates and overlaps.
    """
    if name not in PROVIDERS:
        raise ValueError('No apple_z2 calibration provider for ' + repr(name))
    if any(item['provider'] == name for item in providers):
        raise ValueError('Duplicate calibration provider: ' + name)
    maximum = uint(maximum, 'calibration maximum', 0x40000)
    if not maximum:
        raise ValueError('Empty calibration range: ' + name)
    address = uint(address, 'calibration address')
    if not address:
        raise ValueError('Calibration address must be nonzero: ' + name)
    end = address + ((maximum+3)&~3)
    if end > 0x100000000 or any(address < item['address'] + ((item['maximum']+3)&~3) and item['address'] < end
                                for item in providers):
        raise ValueError('Overlapping or overflowing calibration ranges')
    providers.append({'provider': name, 'maximum': maximum, 'address': address})
    return CALIBRATION, struct.pack('<III', address, maximum, PROVIDERS[name])


def convert(profile, config, *, packet, load_address, hbpp_version):
    if profile.get('PreconstructedBootloadPacketType') != packet or config.get('Protocol') != 'Z2':
        raise ValueError('Unexpected packet type or protocol')
    payload = profile.get('Constructed Firmware')
    if not isinstance(payload, bytes):
        raise ValueError('Expected data-form constructed firmware')
    firmware_address, firmware_data = decode_blob(payload)
    if firmware_address != load_address:
        raise ValueError('Unexpected firmware load address')
    calibration = config.get('Calibration Sequence', [])
    if not isinstance(calibration, list):
        raise ValueError('Unexpected calibration sequence shape')
    names = [item.get('Provider') for item in calibration]
    if not set(names) <= set(PROVIDERS) or len(set(names)) != len(names):
        raise ValueError('Duplicate or unsupported calibration provider')
    if not isinstance(config.get('Power Off On Reset'), bool):
        raise ValueError('Power Off On Reset must be a boolean')
    spi = config['SPI Config']
    timeout = config['Boot Timeout']
    # Preserve source scalar values. CS Delay=10000 and Clock Period Ms=41
    # are raw metadata, not independently established Linux units.
    words = [(1<<9)-1 | (1<<11), uint(config['Min DMA Transfer Size'], 'DMA minimum'),
             uint(config['Z2 Config']['Inter Packet Delay'], 'Z2 delay'),
             uint(spi['CS Delay'], 'raw CS delay'), uint(spi['Clock Phase'], 'CPHA', 1),
             uint(spi['Clock Polarity'], 'CPOL', 1), uint(spi['Word Delay'], 'word delay'),
             uint(spi['Clock Period Ms'], 'raw clock period'),
             int(config['Power Off On Reset'] is True),
             uint(timeout['Normal Boot Ms'], 'normal timeout', 30000), uint(hbpp_version, 'HBPP version', 0xffff)]
    first_boot = uint(timeout['First Boot Ms'], 'first timeout', 30000)
    if not first_boot:
        raise ValueError('First-boot timeout must be nonzero')
    records = [(CONFIG, struct.pack('<11I', *words)),
               (INIT, b'\x1a\xa1\x18\xe1'), (SEND, payload)]
    providers = []
    for item in calibration:
        records.append(calibration_record(item['Provider'], item['Address'], item['Max Size'], providers))
    boot = config.get('Boot Sequence')
    if not isinstance(boot, list):
        raise ValueError('Unexpected boot sequence shape')
    for item in boot:
        if not isinstance(item, dict) or not {'Address', 'Value'} <= item.keys() or set(item)-{'Address','Value','Mask'}:
            raise ValueError('Unsupported boot operation')
        records.append((SEND, register_packet(item['Address'], item.get('Mask', 0xffffffff), item['Value'])))
    records.append((WAIT, struct.pack('<I', first_boot)))
    output = struct.pack('<II', MAGIC, 1) + b''.join(command(c, p) for c, p in records)
    if decode_container(output) != records:
        raise ValueError('Container roundtrip mismatch')
    return output, {'packet': packet, 'hbpp_version': hbpp_version,
                    'firmware_packet_preserved_verbatim': True, 'firmware_packet_bytes': len(payload),
                    'firmware_payload_bytes': len(firmware_data), 'firmware_address': firmware_address,
                    'calibration': providers, 'register_operations': len(boot),
                    'command_sequence': [c for c, p in records]}


# makez2fw (mtfw.c) conversion of Gen2 mtprops and MTFW HBPP item lists.

HBPP_INIT = b'\x1a\xa1\x18\xe1'
REQUEST_CALIBRATION = b'\x1f\x01'
DEFAULT_WAIT_MS = 1000
RX_BIT_REVERSE = 1


def encode_blob(address, data):
    """HBPP14 blob packet loading data at address (makez2fw mtfw_item_add_calload).

    The length field is the number of four-byte words minus one; decode_blob() inverts it.
    """
    if not isinstance(data, bytes) or not data:
        raise ValueError('Empty HBPP payload')
    padded = data + b'\0' * (-len(data) % 4)
    address = uint(address, 'HBPP address')
    if len(padded) > 0x40000 or address + len(padded) > 0x100000000:
        raise ValueError('HBPP payload too large')
    header = struct.pack('>H', len(padded) // 4 - 1) + xe32(address)
    return (b'\x18\xe1\x30\x01' + header + struct.pack('>H', sum(header)) + swapped(padded)
            + xe32(sum(data) & 0xffffffff))


def raw_xfer(tx, rx_size, flags=RX_BIT_REVERSE):
    """Z2FW command 5 payload: tx length, rx length, flags, then the tx bytes."""
    if len(tx) > 16 or rx_size > 16 or not (tx or rx_size):
        raise ValueError('Invalid raw transfer')
    return struct.pack('<III', len(tx), rx_size, flags) + tx


def register_read(address):
    """HBPP register read: request, then an exchange that clocks the answer in."""
    fields = xe32(uint(address, 'register address'))
    request = b'\x1c\x73' + fields + struct.pack('>H', sum(fields))
    return [(RAW_XFER, raw_xfer(request, 0)), (RAW_XFER, raw_xfer(HBPP_INIT + b'\x18\xe1' * 2, 8))]


def otp_preflight_records(address, index, size):
    """makez2fw's OTP read sequence before the first load (mtfw_item_add_otp_preflight)."""
    if not address or not size or size & 3 or size > 256:
        raise ValueError('Invalid OTP address or selection')

    def write(offset, value):
        return SEND, register_packet(address + offset, 0xffffffff, value)

    records = [write(0x00, 2), write(0x04, 0), write(0x08, 0)]
    for word in range(size >> 2):
        records += [write(0x10, 0xffffffff), write(0x0c, index + word),
                    write(0x04, 0x00e80000), write(0x04, 0x00e80001)]
        records += register_read(address + 0x20) + [write(0x20, 1)] + register_read(address + 0x18)
    return records


def hbpp_config(config, *, overrides):
    """makez2fw's configuration words, valid mask and boot wait.

    Only present fields set their valid bit. overrides reads the MTFW's
    "Device Tree Overrides" (makez2fw does so only for MTFW item lists).
    Returns (valid, words or None, wait_ms, (otp address, index, size) or None).
    """
    if not isinstance(config, dict):
        raise ValueError('Unexpected firmware config shape')
    valid, values = 0, [0] * 11

    def section(key):
        value = config.get(key, {})
        if not isinstance(value, dict):
            raise ValueError('Unexpected config section: ' + key)
        return value

    z2, spi, timeout = section('Z2 Config'), section('SPI Config'), section('Boot Timeout')
    fields = [(config, 'Min DMA Transfer Size', 0, 1), (z2, 'Inter Packet Delay', 1, 2),
              (spi, 'CS Delay', 2, 3), (spi, 'Clock Phase', 3, 4), (spi, 'Clock Polarity', 4, 5),
              (spi, 'Word Delay', 5, 6), (spi, 'Clock Period Ms', 6, 7), (timeout, 'Normal Boot Ms', 8, 9)]
    for container, key, bit, index in fields:
        if key in container:
            values[index] = uint(container[key], key)
            valid |= 1 << bit
    if 'Power Off On Reset' in config:
        if not isinstance(config['Power Off On Reset'], bool):
            raise ValueError('Power Off On Reset must be a boolean')
        values[8] = int(config['Power Off On Reset'])
        valid |= 1 << 7
    otp = None
    if overrides:
        dto = section('Device Tree Overrides')

        def data(key, minimum):
            value = dto.get(key)
            return value if isinstance(value, bytes) and len(value) >= minimum else None

        otp_address, chip_id, hbpp, otp_sn = (data('otp-address', 4), data('chip-id-address', 4),
                                              data('hbpp-version', 4), data('otp-sn', 8))
        for value, bit in ((otp_address, 9), (chip_id, 10), (hbpp, 11), (otp_sn, 12),
                           (data('reset-sequence', 1), 13), (data('power-sequence', 1), 14)):
            if value is not None:
                valid |= 1 << bit
        if hbpp is not None:
            values[10] = struct.unpack_from('<I', hbpp)[0]
        if otp_address is not None and otp_sn is not None:
            otp = (struct.unpack_from('<I', otp_address)[0],) + struct.unpack_from('<II', otp_sn)
    values[0] = valid
    wait_ms = values[9] if valid & 1 << 8 and values[9] else DEFAULT_WAIT_MS
    return valid, struct.pack('<11I', *values) if valid else None, wait_ms, otp


def container(records):
    output = struct.pack('<II', MAGIC, 1) + b''.join(command(c, p) for c, p in records)
    if decode_container(output) != records:
        raise ValueError('Container roundtrip mismatch')
    return output


def convert_gen1(profile, *, packet, touch_maximum, prox_maximum, fll_mval, ref_clk_div, clk32_clock_enable):
    """Legacy Z2 data-form mtprops with dynamic per-unit calibration.

    Corellium projectsandcastle 03db9c6ae04141eb940f3b9f56d446f50d57fadf,
    hx-touchd/mtfw/mtfw.c:169-219 defines this Gen1 command order. INIT
    preserves its unacknowledged exchanges; SEND requests a separate ACK.
    Calibration records replace its SysCfg loads at the same positions.
    The N61 packet uses full payload-word counts, before HBPP 0x104.
    Board recipes supply the clock values from Apple's matching kernel
    personality; the legacy mtprops packet contains no Firmware Config.
    """
    if packet != 'Z2' or profile.get('PreconstructedBootloadPacketType') != packet:
        raise ValueError('Unexpected Gen1 packet type')
    payload = profile.get('Constructed Firmware')
    if not isinstance(payload, bytes):
        raise ValueError('Expected data-form Gen1 constructed firmware')
    if 'Firmware Config' in profile:
        raise ValueError('Gen1 firmware with an unsupported config')
    address, firmware = decode_blob(payload, hbpp_version=0x103)
    if address != 0:
        raise ValueError('Unexpected Gen1 firmware load address')
    providers = []
    records = [(INIT, b'\x19\xc1')] + [(INIT, HBPP_INIT + b'\x18\xe1' * 6)] * 3
    records.append(calibration_record('prox-calibration', 0x10009600, prox_maximum, providers))
    records.append(calibration_record('multi-touch-calibration', 0x10009000, touch_maximum, providers))
    records.append((SEND, payload))
    boot = [(0x10003060, uint(fll_mval, 'FLL multiplier')),
            (0x1000305c, uint(ref_clk_div, 'reference clock divider')), (0x10003058, 6),
            (0x10003000, 3), (0x10003518, uint(clk32_clock_enable, '32K clock enable'))]
    records += [(SEND, register_packet(address, 0xffffffff, value)) for address, value in boot]
    records += [(SEND, REQUEST_CALIBRATION), (INIT, bytes.fromhex('1d5334001000000100000045'))]
    return container(records), {'packet': packet, 'generation': 1,
                                'firmware_packet_preserved_verbatim': True,
                                'firmware_packet_bytes': len(payload), 'firmware_payload_bytes': len(firmware),
                                'firmware_address': address, 'calibration': providers,
                                'register_operations': len(boot), 'command_sequence': [c for c, p in records]}


def convert_packets(profile, config, *, packet):
    """mtprops whose constructed firmware is an array of preconstructed packets.

    Same commands, order and words as makez2fw --dynamic-calibration: init,
    the packets verbatim, config, calibration records, boot sequence, wait.
    """
    if profile.get('PreconstructedBootloadPacketType') != packet:
        raise ValueError('Unexpected packet type')
    packets = profile.get('Constructed Firmware')
    if not isinstance(packets, list) or not packets or \
            not all(isinstance(item, bytes) and item for item in packets):
        raise ValueError('Expected array-form constructed firmware')
    if not isinstance(config, dict):
        raise ValueError('Unexpected firmware config shape')
    nested = config.get('Config')
    valid, words, wait_ms, _ = hbpp_config(nested if isinstance(nested, dict) else config, overrides=False)
    records = [(INIT, HBPP_INIT)] + [(SEND, item) for item in packets]
    if words:
        records.append((CONFIG, words))
    calibration = config.get('Calibration Sequence')
    boot = config.get('Boot Sequence')
    if not isinstance(calibration, list) or not isinstance(boot, list):
        raise ValueError('Unexpected calibration or boot sequence shape')
    providers = []
    for item in calibration:
        if not isinstance(item, dict) or not {'Provider', 'Address', 'Max Size'} <= item.keys():
            raise ValueError('Incomplete calibration sequence item')
        records.append(calibration_record(item['Provider'], item['Address'], item['Max Size'], providers))
    for item in boot:
        if isinstance(item, dict) and set(item) == {'Action'} and item['Action'] == 'RequestCalibration':
            records.append((SEND, REQUEST_CALIBRATION))
        elif isinstance(item, dict) and {'Address', 'Value'} <= item.keys() and \
                not set(item) - {'Address', 'Value', 'Mask'}:
            records.append((SEND, register_packet(item['Address'], item.get('Mask', 0xffffffff), item['Value'])))
        else:
            raise ValueError('Unsupported boot operation')
    records.append((WAIT, struct.pack('<I', wait_ms)))
    return container(records), {'packet': packet, 'packets': len(packets), 'config_valid': valid,
                                'calibration': providers, 'boot_operations': len(boot), 'wait_ms': wait_ms,
                                'command_sequence': [c for c, p in records]}


def convert_mtfw(mtfw, profile, *, otp_preflight):
    """MTFW personality listing HBPP load items (ReadModifyWrite, Binary, Property, Config).

    Same commands, order and words as makez2fw, except that each Property
    (per-unit calibration, which makez2fw loads from SysCfg) loads Apple's
    default payload, then a calibration record. apple_z2 skips a record
    whose DT property is missing, so such a unit keeps the default.
    """
    items = mtfw.get(profile) if isinstance(mtfw, dict) else None
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError('MTFW personality not found: ' + str(profile))
    configs = [item.get('Config') for item in items if item.get('Type') == 'Config']
    if len(configs) != 1 or not isinstance(configs[0], dict) or configs[0].get('Protocol') != 'Z2':
        raise ValueError('Expected one Z2 config item')
    valid, words, wait_ms, otp = hbpp_config(configs[0], overrides=True)
    records = [(CONFIG, words)] if words else []
    records.append((INIT, HBPP_INIT))
    if otp_preflight and otp:
        records += otp_preflight_records(*otp)
    providers = []
    loads = []
    for item in items:
        kind = item.get('Type')
        if kind == 'Config':
            continue
        address = uint(item.get('Address'), 'HBPP item address')
        if kind == 'ReadModifyWrite':
            if 'Value' not in item:
                raise ValueError('ReadModifyWrite without value')
            records.append((SEND, register_packet(address, item.get('Mask', 0xffffffff), item['Value'])))
        elif kind == 'Binary':
            if item.get('SysConfigKey'):
                raise ValueError('Binary item with per-unit SysCfg data')
            records.append((SEND, encode_blob(address, item.get('Payload'))))
            loads.append({'address': address, 'bytes': len(item['Payload'])})
        elif kind == 'Property':
            if item.get('Payload'):
                records.append((SEND, encode_blob(address, item['Payload'])))
            records.append(calibration_record(item.get('Property'), address, item.get('MaxSize'), providers))
        else:
            raise ValueError('Unsupported HBPP item type: ' + str(kind))
    records.append((WAIT, struct.pack('<I', wait_ms)))
    return container(records), {'profile': profile, 'config_valid': valid, 'otp_preflight': bool(otp_preflight and otp),
                                'loads': loads, 'calibration': providers, 'wait_ms': wait_ms,
                                'command_sequence': [c for c, p in records]}

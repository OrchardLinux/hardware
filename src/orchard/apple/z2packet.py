"""Z2FW for multitouch profiles that carry one preconstructed Z2 boot packet.

Some profiles (iPad 5: C1F15,1) carry one complete, prefixed boot packet as
"Constructed Firmware" with PreconstructedBootloadPacketType "Z2" instead of
HBPP load items. The packet is checked and wrapped unmodified into a Z2FW
container: SPI mode, the unit's calibration (sent before the firmware, as
the iPad 5 bootloader expects), then the packet.
"""
import plistlib
import struct

from orchard.apple import z2fw

# SPI mode bits in the Z2FW config record's valid mask (apple_z2.c).
CONFIG_CPHA, CONFIG_CPOL = 1 << 3, 1 << 4

PREFIX = b'\x18\xe1\x30\x01'


def halfword32(data: bytes) -> int:
    return int.from_bytes(bytes((data[1], data[0], data[3], data[2])), 'little')


def validate(packet: bytes) -> None:
    if len(packet) < 20 or packet[:4] != PREFIX:
        raise ValueError('Not a prefixed Z2 boot packet')
    if int.from_bytes(packet[4:6], 'big') * 4 + 16 != len(packet):
        raise ValueError('Z2 boot packet length mismatch')
    if halfword32(packet[6:10]):
        raise ValueError('Unexpected Z2 boot packet load address')
    if sum(packet[4:10]) != int.from_bytes(packet[10:12], 'big'):
        raise ValueError('Z2 boot packet header checksum mismatch')
    if sum(packet[12:-4]) & 0xffffffff != halfword32(packet[-4:]):
        raise ValueError('Z2 boot packet payload checksum mismatch')


def extract(mtprops: bytes, profile: str) -> bytes:
    entry = plistlib.loads(mtprops).get(profile)
    if not isinstance(entry, dict) or entry.get('PreconstructedBootloadPacketType') != 'Z2':
        raise ValueError('No preconstructed Z2 packet in profile ' + profile)
    packet = entry.get('Constructed Firmware')
    if not isinstance(packet, bytes):
        raise ValueError('Profile has no constructed firmware: ' + profile)
    validate(packet)
    return packet


def convert(mtprops: bytes, profile: str, calibration_address: int, calibration_size: int) -> bytes:
    packet = extract(mtprops, profile)
    config = [CONFIG_CPHA | CONFIG_CPOL] + [0] * 10  # SPI mode 0; other fields from the driver
    records = [(z2fw.CONFIG, struct.pack('<11I', *config)),
               z2fw.calibration_record('multi-touch-calibration', calibration_address,
                                       calibration_size, []),
               (z2fw.SEND, packet)]
    return z2fw.container(records)

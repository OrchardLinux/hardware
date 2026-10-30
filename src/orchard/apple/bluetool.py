"""BlueTool's embedded Bluetooth firmware; board recipes and selections are callers' policy."""
import hashlib

WRITE_RAM, LAUNCH_RAM = 0xfc4c, 0xfc4e


def _stream_end(data, start):
    """End of the HCD command stream at start: Write RAM records up to Launch RAM."""
    end = start
    while end + 3 <= len(data):
        opcode = int.from_bytes(data[end:end + 2], 'little')
        length = data[end + 2]
        if opcode not in (WRITE_RAM, LAUNCH_RAM) or end + 3 + length > len(data):
            break
        # Newer streams open with a BRCMcfgS record and may follow each other directly.
        if end != start and data[end + 7:end + 15] == b'BRCMcfgS':
            break
        end += 3 + length
        if opcode == LAUNCH_RAM:
            break
    return end


def hcd_from_binary(data, expected):
    """Select only the SHA-pinned HCD command stream embedded in BlueTool.

    Streams are found by their record structure, so both the newer ones that
    open with a BRCMcfgS record and older ones without it (BCM4350) are found.
    """
    offset = 0
    while True:
        start = data.find(b'\x4c\xfc', offset)
        if start < 0:
            raise ValueError('Pinned Bluetooth firmware not found in BlueTool')
        end = _stream_end(data, start)
        if end == start:
            offset = start + 1
            continue
        if hashlib.sha256(data[start:end]).hexdigest() == expected:
            return data[start:end]
        offset = end

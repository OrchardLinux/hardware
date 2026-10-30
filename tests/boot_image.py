"""Build small GPT disk images with an XBOOTLDR FAT32 partition, for tests.

Minimal FAT32 writer: long file names, subdirectories, contiguous files.
"""
import struct
import uuid
import zlib

XBOOTLDR = uuid.UUID('bc13c2ff-59e6-4262-a352-b275fd6f7172')
ROOT_ARM64 = uuid.UUID('b921b045-1df0-41c3-af44-4c6f280d3fae')


def _short_name(index):
    return (f'F{index:07d}'.encode() + b'   ')[:11]


def _lfn_entries(name, short, attributes_dir):
    checksum = 0
    for byte in short:
        checksum = (((checksum & 1) << 7) + (checksum >> 1) + byte) & 0xff
    encoded = name.encode('utf-16-le') + b'\0\0'
    encoded += b'\xff' * (-len(encoded) % 26)
    parts = [encoded[i:i + 26] for i in range(0, len(encoded), 26)]
    entries = []
    for number, part in enumerate(parts, 1):
        order = number | (0x40 if number == len(parts) else 0)
        entries.append(bytes([order]) + part[:10] + bytes([0x0f, 0, checksum]) + part[10:22] + b'\0\0' + part[22:26])
    return list(reversed(entries))


def fat32(files, *, sector=4096, cluster_sectors=1, total_clusters=4096):
    """files: {'/path/name': bytes}. Returns the partition image."""
    cluster = sector * cluster_sectors
    reserved, fats = 8, 2
    fat_sectors = -(-(total_clusters + 2) * 4 // sector)
    data_start = (reserved + fats * fat_sectors) * sector
    total_sectors = reserved + fats * fat_sectors + total_clusters * cluster_sectors
    fat = [0x0ffffff8, 0x0fffffff] + [0] * total_clusters
    clusters = {}
    next_free = [2]

    def allocate(data):
        count = max(1, -(-len(data) // cluster))
        first = next_free[0]
        for i in range(count):
            fat[first + i] = first + i + 1 if i + 1 < count else 0x0fffffff
            clusters[first + i] = data[i * cluster:(i + 1) * cluster].ljust(cluster, b'\0')
        next_free[0] += count
        return first

    tree = {}
    for path, data in files.items():
        node = tree
        parts = [p for p in path.split('/') if p]
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = data

    counter = [0]

    def write_dir(node):
        raw = b''
        for name, value in node.items():
            counter[0] += 1
            short = _short_name(counter[0])
            if isinstance(value, dict):
                first, size, attributes = write_dir(value), 0, 0x10
            else:
                first, size, attributes = allocate(value), len(value), 0x20
            raw += b''.join(_lfn_entries(name, short, attributes))
            raw += short + bytes([attributes]) + bytes(8) + struct.pack('<H', first >> 16) + bytes(4) \
                + struct.pack('<HI', first & 0xffff, size)
        return allocate(raw)

    root = write_dir(tree)
    bpb = bytearray(sector)
    bpb[0:3] = b'\xeb\x58\x90'
    bpb[3:11] = b'ORCHARD '
    struct.pack_into('<HBHBHHBHHHII', bpb, 11, sector, cluster_sectors, reserved, fats, 0, 0, 0xf8, 0, 0, 0, 0,
                     total_sectors)
    struct.pack_into('<IHHIHH', bpb, 36, fat_sectors, 0, 0, root, 1, 6)
    bpb[66] = 0x29
    bpb[82:90] = b'FAT32   '
    bpb[510:512] = b'\x55\xaa'
    image = bytearray(total_sectors * sector)
    image[0:sector] = bpb
    fat_bytes = struct.pack(f'<{len(fat)}I', *fat).ljust(fat_sectors * sector, b'\0')
    for i in range(fats):
        start = (reserved + i * fat_sectors) * sector
        image[start:start + len(fat_bytes)] = fat_bytes
    for number, data in clusters.items():
        start = data_start + (number - 2) * cluster
        image[start:start + cluster] = data
    return bytes(image)


def gpt_disk(partitions, *, block=4096):
    """partitions: [(type uuid, part uuid, name, image bytes)]. Returns a disk image."""
    first = 6
    layout, lba = [], first
    for type_guid, part_guid, name, image in partitions:
        blocks = -(-len(image) // block)
        layout.append((type_guid, part_guid, name, image, lba, lba + blocks - 1))
        lba += blocks
    disk = bytearray((lba + 6) * block)
    table = bytearray(128 * 128)
    for index, (type_guid, part_guid, name, image, start, end) in enumerate(layout):
        entry = type_guid.bytes_le + part_guid.bytes_le + struct.pack('<QQQ', start, end, 0) + \
            name.encode('utf-16-le').ljust(72, b'\0')
        table[index * 128:(index + 1) * 128] = entry
        disk[start * block:start * block + len(image)] = image
    header = bytearray(92)
    header[0:8] = b'EFI PART'
    struct.pack_into('<IIIIQQQQ16sQIII', header, 8, 0x10000, 92, 0, 0, 1, lba + 5, first, lba - 1,
                     uuid.uuid4().bytes_le, 2, 128, 128, zlib.crc32(table))
    disk[block:block + 92] = header
    disk[2 * block:2 * block + len(table)] = table
    return bytes(disk)

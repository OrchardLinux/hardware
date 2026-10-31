"""Read-only boot contract reader: GPT, the XBOOTLDR FAT32 partition and BLS entries.

Everything goes through a block-read callable, so the same code reads a
device through m1n1's main_storage calls or a disk image in tests. Nothing
here writes. See hardware docs/boot-contract.md.
"""
import re
import struct
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

XBOOTLDR = uuid.UUID('bc13c2ff-59e6-4262-a352-b275fd6f7172')
ROOT_ARM64 = uuid.UUID('b921b045-1df0-41c3-af44-4c6f280d3fae')
MAX_ENTRY_FILE = 64 * 1024
MAX_ENTRIES = 64

ReadBlock = Callable[[int], bytes]


class BootDiskError(ValueError):
    pass


@dataclass
class Partition:
    index: int
    type_guid: uuid.UUID
    guid: uuid.UUID
    first: int
    last: int
    name: str


@dataclass
class Entry:
    id: str
    keys: Dict[str, List[str]]
    problems: List[str] = field(default_factory=list)

    def get(self, key: str) -> Optional[str]:
        values = self.keys.get(key)
        return values[0] if values else None

    @property
    def title(self) -> str:
        return self.get('title') or self.id

    @property
    def root(self) -> Optional[str]:
        match = re.search(r'(?:^|\s)root=PARTUUID=([0-9A-Fa-f-]{36})(?:\s|$)', self.get('options') or '')
        return match.group(1).lower() if match else None


class Disk:
    """Block device with a small cache; block_size is the device's logical block size."""

    def __init__(self, read_block: ReadBlock, block_size: int = 4096):
        self.read_block, self.block_size = read_block, block_size
        self.cache: Dict[int, bytes] = {}

    def block(self, lba: int) -> bytes:
        if lba not in self.cache:
            data = self.read_block(lba)
            if len(data) != self.block_size:
                raise BootDiskError(f'Short read at block {lba}')
            if len(self.cache) > 4096:
                self.cache.clear()
            self.cache[lba] = data
        return self.cache[lba]

    def read(self, offset: int, length: int) -> bytes:
        out = bytearray()
        while length:
            lba, skip = divmod(offset, self.block_size)
            chunk = self.block(lba)[skip:skip + length]
            out += chunk
            offset += len(chunk)
            length -= len(chunk)
        return bytes(out)


def partitions(disk: Disk) -> List[Partition]:
    header = disk.block(1)
    if header[:8] != b'EFI PART':
        raise BootDiskError('No GPT on this disk')
    entries_lba, count, size = struct.unpack_from('<QII', header, 72)
    if not 128 <= size <= 4096 or count > 1024:
        raise BootDiskError('Unsupported GPT entry layout')
    table = disk.read(entries_lba * disk.block_size, count * size)
    result = []
    for index in range(count):
        raw = table[index * size:(index + 1) * size]
        if raw[:16] == bytes(16):
            continue
        first, last = struct.unpack_from('<QQ', raw, 32)
        result.append(Partition(index + 1, uuid.UUID(bytes_le=raw[:16]), uuid.UUID(bytes_le=raw[16:32]),
                                first, last, raw[56:128].decode('utf-16-le', 'replace').rstrip('\0')))
    return result


class Fat32:
    """Read-only FAT32 on a partition: directory lookup with long names, file extents."""

    def __init__(self, disk: Disk, partition: Partition):
        self.disk, self.base = disk, partition.first * disk.block_size
        self.limit = (partition.last + 1) * disk.block_size
        bpb = disk.read(self.base, 512)
        if bpb[510:512] != b'\x55\xaa':
            raise BootDiskError('Boot partition has no FAT boot sector')
        sector, per_cluster, reserved, fats = struct.unpack_from('<HBHB', bpb, 11)
        root_entries, total16 = struct.unpack_from('<HH', bpb, 17)
        fat16 = struct.unpack_from('<H', bpb, 22)[0]
        total32, fat32 = struct.unpack_from('<II', bpb, 32)
        self.root = struct.unpack_from('<I', bpb, 44)[0]
        if (sector not in (512, 1024, 2048, 4096) or per_cluster == 0 or per_cluster & (per_cluster - 1)
                or fats == 0 or root_entries or fat16 or not fat32):
            raise BootDiskError('Boot partition is not FAT32')
        self.sector, self.cluster = sector, sector * per_cluster
        self.fat = self.base + reserved * sector
        self.data = self.fat + fats * fat32 * sector
        self.clusters = ((total16 or total32) - (self.data - self.base) // sector) // per_cluster

    def next_cluster(self, cluster: int) -> int:
        return struct.unpack('<I', self.disk.read(self.fat + 4 * cluster, 4))[0] & 0x0fffffff

    def chain(self, cluster: int) -> List[int]:
        result = []
        while 2 <= cluster < 0x0ffffff8:
            if cluster >= self.clusters + 2 or len(result) > self.clusters:
                raise BootDiskError('Corrupt FAT cluster chain')
            result.append(cluster)
            cluster = self.next_cluster(cluster)
        return result

    def offset(self, cluster: int) -> int:
        return self.data + (cluster - 2) * self.cluster

    def extents(self, cluster: int, size: int) -> List[Tuple[int, int]]:
        """(partition-independent disk byte offset, length) runs of a file, merged."""
        runs: List[Tuple[int, int]] = []
        remaining = size
        for c in self.chain(cluster):
            if not remaining:
                break
            length = min(self.cluster, remaining)
            start = self.offset(c)
            if start + length > self.limit:
                raise BootDiskError('File extends past its partition')
            if runs and runs[-1][0] + runs[-1][1] == start:
                runs[-1] = (runs[-1][0], runs[-1][1] + length)
            else:
                runs.append((start, length))
            remaining -= length
        if remaining:
            raise BootDiskError('File is shorter than its directory entry')
        return runs

    def listdir(self, cluster: int) -> Dict[str, Tuple[bool, int, int]]:
        """Lower-cased name -> (is_directory, first cluster, size)."""
        raw = b''.join(self.disk.read(self.offset(c), self.cluster) for c in self.chain(cluster))
        result, long_name = {}, []
        for i in range(0, len(raw), 32):
            entry = raw[i:i + 32]
            if entry[0] == 0:
                break
            if entry[0] == 0xe5:
                long_name = []
                continue
            attributes = entry[11]
            if attributes == 0x0f:
                long_name.insert(0, (entry[1:11] + entry[14:26] + entry[28:32]).decode('utf-16-le', 'replace'))
                continue
            if attributes & 0x08:
                long_name = []
                continue
            if long_name:
                name = ''.join(long_name).split('\0', 1)[0].rstrip('￿')
            else:
                base, ext = entry[0:8].decode('ascii', 'replace').rstrip(), entry[8:11].decode('ascii', 'replace').rstrip()
                name = base + ('.' + ext if ext else '')
            long_name = []
            if name in ('.', '..'):
                continue
            high, low = struct.unpack_from('<H', entry, 20)[0], struct.unpack_from('<H', entry, 26)[0]
            result[name.lower()] = (bool(attributes & 0x10), high << 16 | low, struct.unpack_from('<I', entry, 28)[0])
        return result

    def lookup(self, path: str) -> Tuple[bool, int, int]:
        parts = [part for part in path.split('/') if part]
        if not parts or any(part in ('.', '..') for part in parts):
            raise BootDiskError(f'Invalid path {path!r}')
        node = (True, self.root, 0)
        for part in parts:
            if not node[0]:
                raise BootDiskError(f'{path}: not a directory')
            listing = self.listdir(node[1])
            if part.lower() not in listing:
                raise BootDiskError(f'{path}: missing')
            node = listing[part.lower()]
        return node

    def file(self, path: str) -> Tuple[List[Tuple[int, int]], int]:
        directory, cluster, size = self.lookup(path)
        if directory:
            raise BootDiskError(f'{path}: is a directory')
        return self.extents(cluster, size), size

    def read_file(self, path: str, limit: int) -> bytes:
        runs, size = self.file(path)
        if size > limit:
            raise BootDiskError(f'{path}: too large')
        return b''.join(self.disk.read(start, length) for start, length in runs)


def parse_entry(entry_id: str, text: str) -> Entry:
    keys: Dict[str, List[str]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, _, value = line.partition(' ')
        keys.setdefault(key, []).append(value.strip())
    return Entry(entry_id, keys)


def _version_key(version: str):
    return [(0, int(part), '') if part.isdigit() else (1, 0, part) for part in re.findall(r'\d+|[A-Za-z]+', version)]


def sort_entries(entries: List[Entry]) -> List[Entry]:
    """BLS order: entries with sort-key first by (sort-key, machine-id, version descending)."""
    keyed = [e for e in entries if e.get('sort-key')]
    plain = [e for e in entries if not e.get('sort-key')]
    keyed.sort(key=lambda e: _version_key(e.get('version') or ''), reverse=True)
    keyed.sort(key=lambda e: (e.get('sort-key'), e.get('machine-id') or ''))
    plain.sort(key=lambda e: e.id, reverse=True)
    return keyed + plain


def read_boot(disk: Disk, devicetree: Optional[str] = None):
    """Return (partitions, fat, entries) with every contract violation recorded per entry."""
    parts = partitions(disk)
    boots = [p for p in parts if p.type_guid == XBOOTLDR]
    if len(boots) != 1:
        raise BootDiskError('Expected exactly one XBOOTLDR boot partition, found %d' % len(boots))
    fat = Fat32(disk, boots[0])
    roots = {str(p.guid) for p in parts if p.type_guid == ROOT_ARM64}
    try:
        directory, cluster, _ = fat.lookup('/loader/entries')
    except BootDiskError:
        return parts, fat, []
    if not directory:
        raise BootDiskError('/loader/entries is not a directory')
    names = sorted(name for name, (is_dir, _, _) in fat.listdir(cluster).items()
                   if not is_dir and name.endswith('.conf') and not name.startswith('.'))
    entries = []
    for name in names[:MAX_ENTRIES]:
        entry_id = name[:-len('.conf')]
        try:
            entry = parse_entry(entry_id, fat.read_file('/loader/entries/' + name, MAX_ENTRY_FILE).decode('utf-8'))
        except (BootDiskError, UnicodeDecodeError) as error:
            entries.append(Entry(entry_id, {}, [str(error)]))
            continue
        validate(entry, fat, roots, devicetree)
        entries.append(entry)
    return parts, fat, sort_entries(entries)


def validate(entry: Entry, fat: Fat32, roots, devicetree: Optional[str]) -> None:
    for key in ('devicetree-overlay', 'efi', 'uki'):
        if key in entry.keys:
            entry.problems.append(f'{key} is not supported')
    if entry.get('architecture') not in (None, 'aa64'):
        entry.problems.append('architecture is not aa64')
    for key in ('linux', 'devicetree', 'options'):
        if len(entry.keys.get(key, [])) != 1:
            entry.problems.append(f'needs exactly one {key}')
    for key in ('linux', 'devicetree', 'initrd'):
        for path in entry.keys.get(key, []):
            try:
                fat.file(path)
            except BootDiskError as error:
                entry.problems.append(str(error))
    tree = entry.get('devicetree')
    if devicetree and tree and tree.rsplit('/', 1)[-1] != devicetree:
        entry.problems.append(f'devicetree is not {devicetree}')
    if not entry.root:
        entry.problems.append('options lack root=PARTUUID=')
    elif entry.root not in roots:
        entry.problems.append('root=PARTUUID is not an arm64 root partition on this disk')

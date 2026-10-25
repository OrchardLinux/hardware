"""Read-only GPT discovery and explicitly authorized partition initialization."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

DISK = Path('/dev/nvme0n1')
SYS = Path('/sys/class/block')
MOUNTS = Path('/proc/self/mountinfo')
SWAPS = Path('/proc/swaps')
LINUX_DATA = '0fc63daf-8483-4772-8e79-3d69d8477de4'
TOOLS = Path('/usr/sbin')


def run(*args, timeout=15, pass_fds=()):
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True,
                          timeout=timeout, pass_fds=pass_fds)


def checked(*args, **kwargs):
    result = run(*args, **kwargs)
    if result.returncode:
        raise RuntimeError('Storage command failed: ' + result.stderr.decode(errors='replace')[-2000:])
    return result.stdout


def guid(value):
    parsed = uuid.UUID(value)
    if not parsed.int:
        raise ValueError('Zero storage UUID')
    return str(parsed)


def table():
    result = run(str(TOOLS / 'sfdisk'), '--json', str(DISK))
    # libfdisk warnings include damaged primary/backup GPTs. Do not quietly
    # select a repaired/fallback table for a destructive operation.
    if result.returncode or result.stderr.strip():
        raise ValueError('Cannot read a clean GPT partition table')
    layout = json.loads(result.stdout)['partitiontable']
    sector = int((SYS / DISK.name / 'queue/logical_block_size').read_text())
    disk_bytes = int((SYS / DISK.name / 'size').read_text()) * 512
    if (layout['label'] != 'gpt' or layout['device'] != str(DISK) or
            layout['unit'] != 'sectors' or layout['sectorsize'] != sector or
            sector not in (512, 1024, 2048, 4096, 8192, 16384) or
            not 1 < layout['firstlba'] <= layout['lastlba'] < disk_bytes // sector):
        raise ValueError('Unexpected GPT geometry')
    guid(layout['id'])
    ids, nodes, end = set(), set(), layout['firstlba']
    for row in sorted(layout['partitions'], key=lambda p: p['start']):
        identity = guid(row['uuid'])
        guid(row['type'])
        if (identity in ids or row['node'] in nodes or row['size'] <= 0 or
                row['start'] < end or row['start'] + row['size'] - 1 > layout['lastlba']):
            raise ValueError('Ambiguous or overlapping GPT entries')
        ids.add(identity)
        nodes.add(row['node'])
        end = row['start'] + row['size']
    return layout


def fenced(block, *, readonly=True):
    device = (block / 'dev').read_text().strip()
    mounted = {line.split()[2] for line in MOUNTS.read_text().splitlines()}
    parent = (SYS / DISK.name / 'dev').read_text().strip()
    swaps = []
    for line in SWAPS.read_text().splitlines()[1:]:
        path = Path(line.split()[0])
        if path.exists() and stat.S_ISBLK(path.stat().st_mode):
            swaps.append(f'{os.major(path.stat().st_rdev)}:{os.minor(path.stat().st_rdev)}')
    if (device in mounted or parent in mounted or device in swaps or parent in swaps or
            any((block / 'holders').iterdir()) or any((SYS / DISK.name / 'holders').iterdir()) or
            (block / 'ro').read_text().strip() != ('1' if readonly else '0')):
        raise ValueError('Partition not fenced/unmounted or has active holders')


def probe(path):
    result = run(str(TOOLS / 'blkid'), '--probe', '--output', 'export', str(path))
    if result.stderr.strip() or result.returncode not in (0, 2):
        raise ValueError('Filesystem probe failed or found ambiguous signatures')
    fields = {}
    for line in result.stdout.decode('utf-8').splitlines():
        key, value = line.split('=', 1)
        if key in fields:
            raise ValueError('Duplicate filesystem probe field')
        fields[key] = value
    signatures = {key for key in fields if key != 'DEVNAME' and not key.startswith('PART_ENTRY_')}
    # libblkid can return success for GPT entry metadata alone. Neither that
    # metadata nor DEVNAME is evidence of an existing filesystem signature.
    if not signatures:
        if result.returncode == 0 and 'PART_ENTRY_SCHEME' not in fields:
            raise ValueError('Inconclusive filesystem probe')
        filesystem = 'unformatted'
    elif result.returncode == 2:
        raise ValueError('Inconclusive filesystem probe')
    else:
        filesystem = fields.get('TYPE', 'partition-table' if 'PTTYPE' in fields else 'unsupported')
    return filesystem, fields


def record(block, layout=None):
    layout = table() if layout is None else layout
    path = DISK.parent / block.name
    rows = [p for p in layout['partitions'] if p['node'] == str(path)]
    if len(rows) != 1:
        raise ValueError('Kernel partition is missing from GPT')
    row = rows[0]
    if guid(row['type']) != LINUX_DATA:
        return None
    fields = dict(line.split('=', 1) for line in (block / 'uevent').read_text().splitlines() if '=' in line)
    info = path.lstat()
    size, start = int((block / 'size').read_text()) * 512, int((block / 'start').read_text()) * 512
    major, minor = map(int, (block / 'dev').read_text().split(':'))
    if (not stat.S_ISBLK(info.st_mode) or info.st_rdev != os.makedev(major, minor) or
            guid(fields['PARTUUID']) != guid(row['uuid']) or
            size != row['size'] * layout['sectorsize'] or start != row['start'] * layout['sectorsize']):
        raise ValueError('Kernel and GPT partition identities differ')
    filesystem, signatures = probe(path)
    return {'id': guid(row['uuid']), 'filesystem': filesystem,
            'filesystem_uuid': guid(signatures['UUID']) if filesystem == 'ext4' else None,
            'bytes': size, 'start': start, 'label': row.get('name', ''), 'path': str(path),
            'device_number': info.st_rdev,
            'gpt_sha256': hashlib.sha256(json.dumps(layout, sort_keys=True).encode()).hexdigest(),
            'probe_sha256': hashlib.sha256(json.dumps(signatures, sort_keys=True).encode()).hexdigest()}


def inventory():
    layout = table()
    result = []
    blocks = sorted(SYS.glob(DISK.name + 'p*'))
    if {str(DISK.parent / block.name) for block in blocks} != {p['node'] for p in layout['partitions']}:
        raise ValueError('Kernel partition set differs from GPT')
    for block in blocks:
        fenced(block)
        partition = record(block, layout)
        if partition:
            result.append(partition)
    return result


def format_ext4(partition, filesystem_uuid):
    """Never force/wipe/discard. The caller owns confirmation and attempt state."""
    filesystem_uuid = guid(filesystem_uuid)
    block = SYS / Path(partition['path']).name
    fenced(block)
    if partition['filesystem'] != 'unformatted' or record(block) != partition:
        raise ValueError('Formatting target changed or already has a signature')
    fd = os.open(partition['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISBLK(info.st_mode) or info.st_rdev != partition['device_number']:
            raise ValueError('Opened formatting target identity differs')
        # Retain the original block-device handle for writes and re-fencing;
        # never operate on a subsequently replaced /dev pathname.
        path = '/proc/self/fd/' + str(fd)
        try:
            fenced(block)
            if record(block) != partition:
                raise ValueError('Formatting target changed before writes')
            checked('blockdev', '--setrw', path, pass_fds=(fd,))
            fenced(block, readonly=False)
            checked(str(TOOLS / 'mke2fs'), '-t', 'ext4', '-b', '4096', '-I', '256', '-m', '0',
                    '-U', filesystem_uuid, '-L', 'OrchardLinux',
                    '-O', 'none,has_journal,extent,filetype,sparse_super,large_file,ext_attr,dir_index,metadata_csum,64bit,flex_bg,extra_isize,dir_nlink',
                    '-E', 'nodiscard,lazy_itable_init=0,lazy_journal_init=0', path,
                    pass_fds=(fd,), timeout=120)
            os.fsync(fd)
        finally:
            checked('blockdev', '--setro', path, pass_fds=(fd,))
        fenced(block)
        formatted = record(block)
        identity = ('id', 'bytes', 'start', 'path', 'device_number', 'gpt_sha256')
        if (any(formatted[key] != partition[key] for key in identity) or
                formatted['filesystem'] != 'ext4' or formatted['filesystem_uuid'] != filesystem_uuid):
            raise ValueError('Formatted filesystem could not be verified')
        return formatted
    finally:
        os.close(fd)

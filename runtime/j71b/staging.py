"""Identify and remove only device-owned, incomplete installation staging."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

MARKER = '.orchard-staging.json'
PATTERN = re.compile(r'\.([A-Za-z0-9][A-Za-z0-9._-]{0,63})\.install-[0-9a-f]{32}')


def read_record(path, owner_uid=0):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid or info.st_size > 65536:
            raise ValueError('Invalid staging ownership record')
        data = stream.read(65537)
    return json.loads(data), hashlib.sha256(data).hexdigest()


def identify(path, partition, receipts, firmware_names=(), owner_uid=0):
    match = PATTERN.fullmatch(path.name)
    if not match:
        return None
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner_uid or path.is_mount():
        return None
    marker = path / MARKER
    modern = marker.exists() or marker.is_symlink()
    if not modern:
        marker = path / '.orchard-install.json'
    try:
        record, digest = read_record(marker, owner_uid)
    except (OSError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get('schema') != 1 or record.get('partition') != partition['id'] or record.get('filesystem_uuid') != partition['filesystem_uuid']:
        return None
    if not any(record.get('release') == r['release'] and record.get('root_sha256') == r['sha256'] for r in receipts):
        return None
    if modern:
        if (record.get('profile') != 'j71b' or record.get('status') != 'incomplete' or
                record.get('directory') != match.group(1)):
            return None
    else:
        # Compatibility with verified pre-marker staging, including the old
        # firmware-loop bug. Compatibility digests come from release receipts,
        # never from personal partition layouts or identifiers in source.
        if (record.get('directory') not in (match.group(1), *firmware_names) or
                'previous_directory' in record or not isinstance(record.get('completed_at'), (int, float))):
            return None
    return {'name': path.name, 'inode': info.st_ino, 'device': info.st_dev,
            'record': marker.name, 'digest': digest}


def scan(root, partition, receipts, firmware_names=(), owner_uid=0):
    result = []
    for path in sorted(root.iterdir()):
        candidate = identify(path, partition, receipts, firmware_names, owner_uid)
        if candidate:
            result.append(candidate)
    return result


def remove(root, candidate, partition, receipts, firmware_names=(), mounts=(), owner_uid=0):
    path = root / candidate['name']
    if identify(path, partition, receipts, firmware_names, owner_uid) != candidate:
        raise ValueError('Incomplete staging changed during cleanup')
    if any(str(m) == str(path) or str(m).startswith(str(path) + '/') for m in mounts):
        raise ValueError('Refusing to remove mounted staging')
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('Platform cannot safely remove staging')
    shutil.rmtree(path)

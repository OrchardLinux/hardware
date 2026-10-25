"""Publish the live ADT ECID and kernel boot ID before exposing recovery USB."""
import json
import os
from pathlib import Path
import re
import stat
import uuid

from adt import validate

RUN = Path('/run/orchard')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
MTD = Path('/proc/mtd')
DEV = Path('/dev')


def cached():
    path = RUN / 'identity.json'
    if not path.exists() and not path.is_symlink():
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError('Device identity record is not protected')
    result = json.loads(path.read_text())
    if (result.get('profile') != 'j71b' or type(result.get('ecid')) is not int or
            not 0 < result['ecid'] < 2**64 or
            result.get('boot_id') != str(uuid.UUID(BOOT_ID.read_text().strip()))):
        raise ValueError('Device identity record differs from this boot')
    return result


def initialize():
    if cached() is not None:
        raise ValueError('Device identity was already published')
    rows = re.findall(r'^mtd([0-9]+): ([0-9a-fA-F]+) [0-9a-fA-F]+ "adt"$', MTD.read_text(), re.M)
    if len(rows) != 1:
        raise ValueError('Expected one live ADT MTD entry')
    number, size = rows[0][0], int(rows[0][1], 16)
    if not 8 <= size <= 16 * 1024**2:
        raise ValueError('Live ADT length outside bounds')
    fd = os.open(DEV / ('mtd' + number), os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISCHR(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Live ADT must be an MTD character device')
        blob = stream.read(size)
    if len(blob) != size:
        raise ValueError('Live ADT read was truncated')
    result = {'profile': 'j71b', 'ecid': validate(blob),
              'boot_id': str(uuid.UUID(BOOT_ID.read_text().strip()))}
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RUN.is_symlink():
        raise ValueError('Linked runtime state refused')
    RUN.chmod(0o700)
    fd = os.open(RUN / 'identity.json', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(result, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return result


if __name__ == '__main__':
    record = initialize()
    print(f'ECID:{record["ecid"]:016x} BOOT:{record["boot_id"]}')

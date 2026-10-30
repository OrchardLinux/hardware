#!/usr/bin/env python3
"""Orchard tethered install, run on the device's Linux recovery as root.

    install_device.py inspect
    install_device.py run PLAN.json < rootfs.tar.zst

The CLI copies this one file to the device for each install, so it uses the
standard library and the recovery's sfdisk, blkid, mke2fs, blockdev and
mount only. `inspect` is read-only. `run` installs top to bottom: it
re-checks the live partition table against the plan (made from an earlier
inspect, see install_layout.py), changes only the ranges the plan names,
and writes the boot entry last. It never touches APFS or any partition the
plan does not name. Every step reports one JSON line on stdout and in
/run/orchard/install.log; a run interrupted after its partition-table
write is rerun from a fresh inspect, which then sees the new partitions.

A plan with `account` also sets the system's one user account: the CLI
uploads /run/orchard/secrets.json (root-only, {"password", "pin"}) for the
run, and the script deletes it as soon as it has read it. The password
goes to the account through the system's own chpasswd. The 6-digit
lock-screen PIN is kept apart from it, as a SHA-512 crypt hash in
/etc/orchard/pin (`user:hash`). The PIN signs in at boot (greetd's PAM
service, `greetd`) and unlocks the screen (`omarchy-lock-password`, which
the desktop's lock uses). The lock runs PAM as the user, so the hash is
readable by the user's group and the pam_faillock tally lives in a
directory the user owns: 5 wrong PINs lock it for 5 minutes. The PIN never
unlocks sudo or SSH. Neither secret is ever written to the events or the
log.
"""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import subprocess
import sys
import tarfile
import time
import uuid

LOG = Path('/run/orchard/install.log')
TARGET = Path('/run/orchard/target')
XBOOTLDR = 'bc13c2ff-59e6-4262-a352-b275fd6f7172'
ROOT_ARM64 = 'b921b045-1df0-41c3-af44-4c6f280d3fae'
DEFAULT_DISK = '/dev/nvme0n1'
SECRETS = Path('/run/orchard/secrets.json')
# The recovery loads NVMe at boot and fences it read-only in the background
# (hardware's storage-fence), then leaves one of these markers.
STORAGE_FENCE = Path('/usr/libexec/orchard/storage-fence')
STORAGE_READY = Path('/run/orchard/storage-ready')
STORAGE_FAILED = Path('/run/orchard/storage-failed')

PIN_CHECK = r'''#!/bin/sh
# Lock-screen PIN check for the orchard-pin PAM service (pam_exec expose_authtok).
# The PIN arrives on stdin, NUL-terminated; /etc/orchard/pin holds user:sha512-crypt.
PATH=/usr/bin:/bin
pin=$(tr -d '\000')
case $pin in [0-9][0-9][0-9][0-9][0-9][0-9]) ;; *) exit 1 ;; esac
while IFS=: read -r user hash; do
    [ "$user" = "$PAM_USER" ] || continue
    salt=${hash#\$6\$}
    salt=${salt%%\$*}
    [ "$(printf '%s' "$pin" | openssl passwd -6 -salt "$salt" -stdin)" = "$hash" ]
    exit
done < /etc/orchard/pin
exit 1
'''

PIN_PAM = '''#%PAM-1.0
# Orchard PIN: 6 digits, 5 tries, then locked for 5 minutes. It signs in at
# boot and unlocks the screen only; sudo and SSH never accept it. The lock
# runs this as the user, so the tally lives in a directory the user owns.
auth     requisite                pam_nologin.so
auth     required                 pam_faillock.so preauth dir=/var/lib/orchard/pin-tally deny=5 unlock_time=300
auth     [success=1 default=bad]  pam_exec.so quiet expose_authtok /usr/lib/orchard/pin-check
auth     [default=die]            pam_faillock.so authfail dir=/var/lib/orchard/pin-tally deny=5 unlock_time=300
auth     sufficient               pam_faillock.so authsucc dir=/var/lib/orchard/pin-tally deny=5 unlock_time=300
auth     required                 pam_deny.so
account  include                  system-local-login
session  include                  system-local-login
'''


class InstallError(RuntimeError):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code


def emit(event, **fields):
    line = json.dumps({'event': event, 'time': round(time.time(), 3), **fields}, sort_keys=True)
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open('a') as log:
        log.write(line + '\n')


def run(*argv, input=None, timeout=120):
    result = subprocess.run(argv, input=input, capture_output=True, timeout=timeout)
    if result.returncode:
        raise InstallError('command-failed', f'{argv[0]}: ' + result.stderr.decode(errors='replace')[-1500:])
    return result.stdout


# --- partition table -------------------------------------------------------

def node(disk, number):
    return f'{disk}p{number}' if disk[-1].isdigit() else f'{disk}{number}'


def sector_size(disk):
    return int(Path('/sys/class/block', Path(disk).name, 'queue/logical_block_size').read_text())


def read_gpt(disk):
    block = sector_size(disk)
    with open(disk, 'rb') as dev:
        dev.seek(block)
        header = dev.read(block)
        if header[:8] != b'EFI PART':
            raise InstallError('no-gpt', f'{disk} has no GPT')
        size = struct.unpack_from('<I', header, 12)[0]
        first, last = struct.unpack_from('<QQ', header, 40)
        entries_lba, count, entry_size = struct.unpack_from('<QII', header, 72)
        dev.seek(entries_lba * block)
        table = dev.read(count * entry_size)
    partitions = []
    for index in range(count):
        raw = table[index * entry_size:(index + 1) * entry_size]
        if raw[:16] == bytes(16):
            continue
        start, end = struct.unpack_from('<QQ', raw, 32)
        partitions.append({'number': index + 1, 'start': start, 'blocks': end - start + 1,
                           'type': str(uuid.UUID(bytes_le=raw[:16])),
                           'partuuid': str(uuid.UUID(bytes_le=raw[16:32])),
                           'name': raw[56:128].decode('utf-16-le', 'replace').rstrip('\0')})
    partitions.sort(key=lambda p: p['start'])
    free, cursor = [], first
    for part in partitions:
        if part['start'] > cursor:
            free.append({'start': cursor, 'blocks': part['start'] - cursor})
        cursor = max(cursor, part['start'] + part['blocks'])
    if cursor <= last:
        free.append({'start': cursor, 'blocks': last - cursor + 1})
    digest = hashlib.sha256(header[:size] + table).hexdigest()
    return {'path': disk, 'sector': block, 'first': first, 'last': last, 'table_sha256': digest}, \
        partitions, free


def filesystem(path):
    result = subprocess.run(['blkid', '-o', 'export', path], capture_output=True, timeout=15)
    fields = dict(line.split('=', 1) for line in result.stdout.decode().splitlines() if '=' in line)
    return {'fs': fields.get('TYPE'), 'fs_uuid': fields.get('UUID'), 'label': fields.get('LABEL')}


def mounted_devices():
    numbers = set()
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        numbers.add(line.split()[2])
    return numbers


def check_quiet(disk, partitions):
    """Nothing on the disk may be mounted or held (swap, device mapper, ...)."""
    mounted = mounted_devices()
    base = Path('/sys/class/block', Path(disk).name)
    for entry in [base, *(base / Path(node(disk, p['number'])).name for p in partitions)]:
        if not entry.exists():
            continue
        if (entry / 'dev').read_text().strip() in mounted or any((entry / 'holders').iterdir()):
            raise InstallError('disk-busy', f'{entry.name} is mounted or in use')


# --- inspect -----------------------------------------------------------------

def wait_for_storage(seconds=60):
    """On the recovery, wait until its storage fence has finished; elsewhere (tests) there is none."""
    if not STORAGE_FENCE.exists():
        return
    deadline = time.monotonic() + seconds
    while not STORAGE_READY.exists():
        if STORAGE_FAILED.exists():
            raise InstallError('storage-unavailable', 'Storage did not come up: ' +
                               STORAGE_FAILED.read_text(errors='replace').strip())
        if time.monotonic() >= deadline:
            raise InstallError('storage-unavailable', f'Storage was not ready after {seconds} s')
        time.sleep(0.5)


def inspect(disk):
    info, partitions, free = read_gpt(disk)
    for part in partitions:
        part.update(filesystem(node(disk, part['number'])))
    emit('layout', disk=info, partitions=partitions, free=free)


# --- FAT32 -------------------------------------------------------------------

def make_fat32(path, blocks, sector, volume_id, label=b'BOOT       '):
    """An empty FAT32 filesystem: boot sector, FSInfo, backup, FATs, root cluster."""
    reserved, fats = 8, 2
    total = blocks
    fat_sectors = 1
    while True:
        clusters = total - reserved - fats * fat_sectors
        needed = -(-(clusters + 2) * 4 // sector)
        if needed <= fat_sectors:
            break
        fat_sectors = needed
    if clusters < 65525:
        raise InstallError('boot-too-small', 'Boot partition is too small for FAT32')
    boot = bytearray(sector)
    boot[0:3] = b'\xeb\x58\x90'
    boot[3:11] = b'ORCHARD '
    struct.pack_into('<HBHBHHBHHHII', boot, 11, sector, 1, reserved, fats, 0, 0, 0xf8, 0, 32, 64, 0, total)
    struct.pack_into('<IHHIHH', boot, 36, fat_sectors, 0, 0, 2, 1, 6)
    struct.pack_into('<BBBI', boot, 64, 0x80, 0, 0x29, volume_id)
    boot[71:82] = label
    boot[82:90] = b'FAT32   '
    boot[510:512] = b'\x55\xaa'
    fsinfo = bytearray(sector)
    struct.pack_into('<I', fsinfo, 0, 0x41615252)
    struct.pack_into('<III', fsinfo, 484, 0x61417272, clusters - 1, 3)
    struct.pack_into('<I', fsinfo, 508, 0xaa550000)
    fat = bytearray(fat_sectors * sector)
    struct.pack_into('<III', fat, 0, 0x0ffffff8, 0x0fffffff, 0x0fffffff)
    fd = os.open(path, os.O_WRONLY)
    try:
        for offset, data in ((0, boot), (sector, fsinfo), (6 * sector, boot), (7 * sector, fsinfo)):
            os.pwrite(fd, data, offset)
        for index in range(fats):
            os.pwrite(fd, fat, (reserved + index * fat_sectors) * sector)
        os.pwrite(fd, bytes(sector), (reserved + fats * fat_sectors) * sector)   # root cluster
        os.fsync(fd)
    finally:
        os.close(fd)


# --- safe extraction (rules of hardware runtime/j71b/archive.py) -------------

class Counting:
    def __init__(self, source, limit, step=64 << 20):
        self.source, self.limit, self.count, self.step = source, limit, 0, step
        self.digest, self.reported = hashlib.sha256(), 0

    def read(self, size=-1):
        data = self.source.read(min(size if size >= 0 else 1 << 20, 1 << 20))
        self.count += len(data)
        if self.count > self.limit:
            raise InstallError('stream-too-long', 'Incoming stream exceeds its declared size')
        self.digest.update(data)
        if self.count - self.reported >= self.step:
            self.reported = self.count
            emit('progress', step='extract-root', done=self.count, total=self.limit)
        return data


def safe_name(name):
    if not isinstance(name, str) or '\x00' in name or name.startswith('/'):
        raise InstallError('unsafe-archive', 'Absolute or invalid archive path')
    parts = PurePosixPath(name).parts
    if not parts or '..' in parts:
        raise InstallError('unsafe-archive', 'Archive path escapes the target')
    return '/'.join(parts)


def parent_fd(root, name):
    parts = safe_name(name).split('/')
    current = os.dup(root)
    try:
        for part in parts[:-1]:
            created = False
            try:
                os.mkdir(part, 0o755, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = following
            if created:
                os.fchmod(current, 0o755)
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def extract(source, destination, *, size, sha256, expanded_limit, compression):
    incoming = Counting(source, size)
    root = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    seen, links, directories, expanded = set(), [], [], 0
    try:
        if compression == 'zstd':
            from compression.zstd import ZstdFile
            decoder = ZstdFile(incoming, mode='rb')
        elif compression == 'gzip':
            decoder = gzip.GzipFile(fileobj=incoming, mode='rb')
        else:
            raise InstallError('unsupported', 'Unsupported compression ' + str(compression))
        with decoder as compressed, tarfile.open(fileobj=compressed, mode='r|') as archive:
            for member in archive:
                if member.name in ('.', './') and member.isdir():
                    continue
                name = safe_name(member.name)
                if name in seen:
                    raise InstallError('unsafe-archive', 'Duplicate archive member ' + name)
                seen.add(name)
                if member.issym() or member.islnk():
                    if member.islnk():
                        safe_name(member.linkname)
                    elif '\x00' in member.linkname or not member.linkname:
                        raise InstallError('unsafe-archive', 'Invalid symlink ' + name)
                    links.append((name, member))
                    continue
                if not (member.isdir() or member.isfile()):
                    raise InstallError('unsafe-archive', 'Special archive member ' + name)
                parent, leaf = parent_fd(root, name)
                try:
                    if member.isdir():
                        try:
                            os.mkdir(leaf, 0o755, dir_fd=parent)
                        except FileExistsError:
                            os.close(os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
                        directories.append((name, member))
                        continue
                    expanded += member.size
                    if expanded > expanded_limit:
                        raise InstallError('stream-too-long', 'Expanded archive exceeds its limit')
                    fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as output:
                        content, remaining = archive.extractfile(member), member.size
                        while remaining:
                            chunk = content.read(min(1 << 20, remaining))
                            if not chunk:
                                raise InstallError('stream-truncated', 'Truncated archive member ' + name)
                            output.write(chunk)
                            remaining -= len(chunk)
                        output.flush()
                        os.fchown(output.fileno(), member.uid, member.gid)
                        os.utime(output.fileno(), (member.mtime, member.mtime))
                        os.fchmod(output.fileno(), member.mode & 0o7777)
                finally:
                    os.close(parent)
            while compressed.read(1 << 20):
                pass
        while incoming.read(1 << 20):
            pass
        if incoming.count != size or incoming.digest.hexdigest() != sha256:
            raise InstallError('stream-mismatch', 'Incoming archive size or hash differs from the release')
        for name, member in sorted(links, key=lambda item: item[1].issym()):
            parent, leaf = parent_fd(root, name)
            try:
                if member.islnk():
                    src_parent, src_leaf = parent_fd(root, member.linkname)
                    try:
                        fd = os.open(src_leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_parent)
                        try:
                            if not stat.S_ISREG(os.fstat(fd).st_mode):
                                raise InstallError('unsafe-archive', 'Hardlink to a non-file ' + name)
                        finally:
                            os.close(fd)
                        os.link(src_leaf, leaf, src_dir_fd=src_parent, dst_dir_fd=parent, follow_symlinks=False)
                    finally:
                        os.close(src_parent)
                else:
                    os.symlink(member.linkname, leaf, dir_fd=parent)
                    os.chown(leaf, member.uid, member.gid, dir_fd=parent, follow_symlinks=False)
            finally:
                os.close(parent)
        for name, member in reversed(directories):
            parent, leaf = parent_fd(root, name)
            try:
                fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    os.fchown(fd, member.uid, member.gid)
                    os.utime(fd, (member.mtime, member.mtime))
                    os.fchmod(fd, member.mode & 0o7777)
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
        return {'bytes': incoming.count, 'expanded_bytes': expanded, 'members': len(seen)}
    finally:
        os.close(root)


# --- run ---------------------------------------------------------------------

def check_plan(plan, info, partitions, free):
    """The plan's new partitions lie in free space or the removed partition, nowhere else."""
    if plan.get('version') != 1:
        raise InstallError('plan-version', 'Unsupported plan version')
    if info['table_sha256'] != plan['disk']['table_sha256'] or info['sector'] != plan['disk']['sector']:
        raise InstallError('layout-changed', 'The partition table changed since it was inspected; inspect again')
    allowed = [(f['start'], f['start'] + f['blocks']) for f in free]
    removed = plan.get('remove')
    if removed:
        match = [p for p in partitions if p['number'] == removed['number']]
        if (not match or match[0]['partuuid'] != removed['partuuid'] or
                match[0]['type'] not in (ROOT_ARM64, '0fc63daf-8483-4772-8e79-3d69d8477de4')):
            raise InstallError('plan-mismatch', 'The partition to replace is not the inspected Linux partition')
        allowed.append((match[0]['start'], match[0]['start'] + match[0]['blocks']))
    new = [plan['root']] + ([plan['boot']] if plan['boot']['create'] else [])
    for part in new:
        start, end = part['start'], part['start'] + part['blocks']
        if not any(low <= start and end <= high for low, high in allowed):
            raise InstallError('plan-mismatch', 'A new partition lies outside free space and the replaced partition')
    if not plan['boot']['create'] and not any(p['partuuid'] == plan['boot']['partuuid'] and p['type'] == XBOOTLDR
                                              for p in partitions):
        raise InstallError('plan-mismatch', 'The boot partition to reuse is missing')


def free_numbers(partitions, count):
    used = {p['number'] for p in partitions}
    return [n for n in range(1, 129) if n not in used][:count]


def write_table(disk, plan, partitions):
    keep = [p for p in partitions if not plan.get('remove') or p['number'] != plan['remove']['number']]
    numbers = free_numbers(keep, 2)
    if plan.get('remove'):
        run('sfdisk', '--no-reread', '--delete', disk, str(plan['remove']['number']))
    created = []
    for spec, type_, name in ((plan['boot'], XBOOTLDR, 'boot'), (plan['root'], ROOT_ARM64, plan['root']['label'])):
        if spec is plan['boot'] and not spec['create']:
            continue
        number = numbers.pop(0)
        line = f'start={spec["start"]}, size={spec["blocks"]}, type={type_}, uuid={spec["partuuid"]}, name="{name}"\n'
        run('sfdisk', '--no-reread', '-N', str(number), disk, input=line.encode())
        created.append((number, spec))
    subprocess.run(['blockdev', '--rereadpt', disk], capture_output=True, timeout=30)
    deadline = time.monotonic() + 15
    while not all(Path(node(disk, n)).exists() for n, _ in created):
        if time.monotonic() > deadline:
            raise InstallError('nodes-missing', 'New partition nodes did not appear')
        time.sleep(0.2)
    _, current, _ = read_gpt(disk)
    for number, spec in created:
        match = [p for p in current if p['number'] == number]
        if not match or (match[0]['start'], match[0]['blocks'], match[0]['partuuid']) != \
                (spec['start'], spec['blocks'], spec['partuuid']):
            raise InstallError('table-verify', f'Partition {number} is not as planned')
    return {spec['partuuid']: number for number, spec in created}


def write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def remove_old_entries(boot_root, partuuid):
    entries = boot_root / 'loader/entries'
    removed = []
    for entry in sorted(entries.glob('*.conf')) if entries.is_dir() else []:
        if f'root=PARTUUID={partuuid}' in entry.read_text(errors='replace'):
            entry.unlink()
            removed.append(entry.name)
    return removed


def check_entry(boot_root, partuuid, devicetree):
    for entry in sorted((boot_root / 'loader/entries').glob('*.conf')):
        keys = {}
        for line in entry.read_text().splitlines():
            key, _, value = line.strip().partition(' ')
            keys.setdefault(key, value.strip())
        if f'root=PARTUUID={partuuid}' not in keys.get('options', ''):
            continue
        for key in ('linux', 'devicetree'):
            if not keys.get(key) or not (boot_root / keys[key].lstrip('/')).is_file():
                raise InstallError('entry-invalid', f'{entry.name}: {key} is missing')
        if PurePosixPath(keys['devicetree']).name != devicetree:
            raise InstallError('entry-invalid', f'{entry.name}: devicetree is not {devicetree}')
        for path in [line.strip().partition(' ')[2] for line in entry.read_text().splitlines()
                     if line.startswith('initrd')]:
            if not (boot_root / path.strip().lstrip('/')).is_file():
                raise InstallError('entry-invalid', f'{entry.name}: initrd {path} is missing')
        return entry.name
    raise InstallError('entry-missing', 'The system did not write a boot entry for its root')


def install_firmware(bundle, root, *, size, sha256):
    """The private firmware bundle (lib/firmware/... plus mixin.json) into /usr/lib/firmware.

    Arch's /lib is a link to usr/lib, which the extractor never follows, so
    firmware paths are mapped explicitly; nothing outside it is written.
    """
    data = bundle.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        raise InstallError('firmware-mismatch', 'Firmware bundle size or hash differs from the plan')
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    count = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
            for member in archive:
                name = safe_name(member.name)
                if name == 'mixin.json':
                    continue
                if not (member.isfile() and name.startswith('lib/firmware/')):
                    raise InstallError('unsafe-archive', 'Firmware bundle may contain only firmware files')
                parent, leaf = parent_fd(root_fd, 'usr/' + name)
                try:
                    fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644, dir_fd=parent)
                    with os.fdopen(fd, 'wb') as output:
                        output.write(archive.extractfile(member).read())
                        os.fchown(output.fileno(), 0, 0)
                        os.fchmod(output.fileno(), 0o644)
                    count += 1
                finally:
                    os.close(parent)
    finally:
        os.close(root_fd)
    return count


# --- account -----------------------------------------------------------------

def read_secrets(path):
    """The password and PIN the CLI uploaded for this run; the file is deleted at once."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise InstallError('no-secrets', f'{path} is missing; the CLI uploads it for each run') from None
    try:
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise InstallError('secrets-unsafe', f'{path} must be a regular file readable by root only')
        secrets = json.loads(path.read_text())
    finally:
        path.unlink()
    password, pin = secrets.get('password'), secrets.get('pin')
    if not isinstance(password, str) or not password or '\n' in password or '\0' in password:
        raise InstallError('bad-password', 'The password is empty or contains a line break')
    if not isinstance(pin, str) or len(pin) != 6 or not (pin.isascii() and pin.isdigit()):
        raise InstallError('bad-pin', 'The PIN must be exactly 6 digits')
    return password, pin


def account_user(root):
    """The system's one regular user (UID 1000-59999): name, uid, gid."""
    users = []
    for line in (root / 'etc/passwd').read_text().splitlines():
        fields = line.split(':')
        if len(fields) == 7 and fields[2].isdigit() and fields[3].isdigit() and 1000 <= int(fields[2]) < 60000:
            users.append((fields[0], int(fields[2]), int(fields[3])))
    if len(users) != 1:
        raise InstallError('account-user', f'The system image needs exactly one regular user, '
                                           f'found {[u[0] for u in users]}')
    return users[0]


def write_file(path, text, mode, group=0):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w') as out:
        os.fchown(out.fileno(), 0, group)
        os.fchmod(out.fileno(), mode)
        out.write(text)


def set_account(root, password, pin):
    user, uid, gid = account_user(root)
    run('chroot', str(root), 'chpasswd', input=f'{user}:{password}\n'.encode())
    digest = run('chroot', str(root), 'openssl', 'passwd', '-6', '-stdin', input=pin.encode()).decode().strip()
    if not digest.startswith('$6$') or ':' in digest or '\n' in digest:
        raise InstallError('pin-hash', 'openssl in the system image did not return a SHA-512 crypt hash')
    write_file(root / 'etc/orchard/pin', f'{user}:{digest}\n', 0o640, group=gid)
    tally = root / 'var/lib/orchard/pin-tally'
    tally.mkdir(parents=True, exist_ok=True)
    os.chown(tally, uid, gid)
    tally.chmod(0o700)
    # greetd checks the PIN as root: an existing user-owned tally keeps the
    # lock screen, which runs as the user, able to read and reset it.
    write_file(tally / user, '', 0o600, group=gid)
    os.chown(tally / user, uid, gid)
    write_file(root / 'usr/lib/orchard/pin-check', PIN_CHECK, 0o755)
    for service in ('greetd', 'omarchy-lock-password'):
        write_file(root / 'etc/pam.d' / service, PIN_PAM, 0o644)
    return user


def kernel_version(root):
    """The image's one kernel: /usr/lib/modules/<version>/vmlinuz."""
    modules = root / 'usr/lib/modules'
    versions = sorted(d.name for d in modules.iterdir() if (d / 'vmlinuz').is_file()) if modules.is_dir() else []
    if len(versions) != 1:
        raise InstallError('no-kernel', 'The system image needs exactly one kernel at '
                                        f'/usr/lib/modules/<version>/vmlinuz, found {versions}')
    return versions[0]


def install(plan, stream):
    disk = plan['disk']['path']
    if os.geteuid() != 0:
        raise InstallError('not-root', 'The installer must run as root')
    secrets = read_secrets(SECRETS) if plan.get('account') else None
    info, partitions, free = read_gpt(disk)
    check_plan(plan, info, partitions, free)
    check_quiet(disk, partitions)
    emit('step', step='check', state='done')
    mounts = []
    run('blockdev', '--setrw', disk)
    try:
        numbers = write_table(disk, plan, partitions)
        emit('step', step='partitions', state='done')
        root_node = node(disk, numbers[plan['root']['partuuid']])
        if plan['boot']['create']:
            boot_node = node(disk, numbers[plan['boot']['partuuid']])
            run('blockdev', '--setrw', boot_node)
            make_fat32(boot_node, plan['boot']['blocks'], info['sector'], int(plan['boot']['fs_uuid'], 16))
            emit('step', step='format-boot', state='done')
        else:
            boot_node = node(disk, plan['boot']['number'])
            run('blockdev', '--setrw', boot_node)
        run('blockdev', '--setrw', root_node)
        run('mke2fs', '-t', 'ext4', '-b', '4096', '-m', '0', '-U', plan['root']['fs_uuid'],
            '-L', plan['root']['label'], '-E', 'nodiscard,lazy_itable_init=0,lazy_journal_init=0',
            root_node, timeout=300)
        emit('step', step='format-root', state='done')
        TARGET.mkdir(parents=True, exist_ok=True)
        run('mount', '-t', 'ext4', root_node, str(TARGET))
        mounts.append(TARGET)
        emit('step', step='extract-root', state='started', total=plan['image']['bytes'])
        result = extract(stream, TARGET, size=plan['image']['bytes'], sha256=plan['image']['sha256'],
                         expanded_limit=plan['root']['blocks'] * info['sector'],
                         compression=plan['image'].get('compression', 'zstd'))
        emit('step', step='extract-root', state='done', **result)
        firmware = plan.get('firmware')
        if firmware:
            with open(firmware['path'], 'rb') as bundle:
                files = install_firmware(bundle, TARGET, size=firmware['bytes'], sha256=firmware['sha256'])
            Path(firmware['path']).unlink()
            emit('step', step='firmware', state='done', files=files)
        boot_mount = TARGET / 'boot'
        boot_mount.mkdir(exist_ok=True)
        run('mount', '-t', 'vfat', boot_node, str(boot_mount))
        mounts.append(boot_mount)
        if plan.get('remove'):
            gone = remove_old_entries(boot_mount, plan['remove']['partuuid'])
            emit('step', step='remove-old-entries', state='done', entries=gone)
        entry = plan['entry']
        write_text(TARGET / 'etc/kernel/cmdline',
                   f'root=PARTUUID={plan["root"]["partuuid"]} rw {entry.get("options", "")}'.strip() + '\n')
        write_text(TARGET / 'etc/kernel/entry-token', entry['id'] + '\n')
        # One root serves every board of its kernel platform; kernel-install
        # copies the DTB this file names (see hardware docs/boot-contract.md).
        write_text(TARGET / 'etc/kernel/devicetree', entry['devicetree'] + '\n')
        write_text(TARGET / 'etc/fstab',
                   f'PARTUUID={plan["root"]["partuuid"]} / ext4 rw,relatime 0 1\n'
                   f'PARTUUID={plan["boot"]["partuuid"]} /boot vfat rw,relatime,fmask=0077,dmask=0077 0 2\n')
        for name, fs in (('proc', 'proc'), ('sys', 'sysfs'), ('dev', 'devtmpfs')):
            point = TARGET / name
            point.mkdir(exist_ok=True)
            run('mount', '-t', fs, name, str(point))
            mounts.append(point)
        if secrets:
            emit('step', step='account', state='done', user=set_account(TARGET, *secrets))
            secrets = None
        version = kernel_version(TARGET)
        kernel = f'/usr/lib/modules/{version}/vmlinuz'
        run('chroot', str(TARGET), 'kernel-install', 'add', version, kernel, timeout=600)
        name = check_entry(boot_mount, plan['root']['partuuid'], entry['devicetree'])
        emit('step', step='boot-entry', state='done', entry=name)
    finally:
        for point in reversed(mounts):
            subprocess.run(['umount', str(point)], capture_output=True, timeout=60)
        subprocess.run(['sync'], timeout=120)
        try:
            nodes = [node(disk, p['number']) for p in read_gpt(disk)[1]]
        except Exception:  # keep the original error; fence what still exists
            nodes = [str(path) for path in Path('/dev').glob(Path(disk).name + 'p*')]
        for path in (disk, *nodes):
            subprocess.run(['blockdev', '--setro', path], capture_output=True, timeout=15)
    emit('finished', root=plan['root']['partuuid'], boot=plan['boot']['partuuid'])


def main(argv):
    disk = os.environ.get('ORCHARD_INSTALL_DISK', DEFAULT_DISK)
    try:
        if argv[1:] == ['inspect']:
            wait_for_storage()
            inspect(disk)
        elif len(argv) == 3 and argv[1] == 'run':
            wait_for_storage()
            install(json.loads(Path(argv[2]).read_text()), sys.stdin.buffer)
        else:
            raise InstallError('usage', 'usage: install_device.py inspect | run PLAN.json < ROOT')
    except InstallError as error:
        emit('error', code=error.code, detail=str(error))
        return 1
    except Exception as error:  # report, never hide, unexpected failures
        emit('error', code='unexpected', detail=f'{type(error).__name__}: {error}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

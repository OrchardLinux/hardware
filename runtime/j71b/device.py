#!/usr/bin/python3
"""Versioned device-side installation API. Hardware/storage details stay here."""
import base64
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import time
import uuid

from archive import extract, safe_name, parent_fd
import staging
import ssh_access
import storage
import identity
from image_policy import (verify_privileges, verify_session_access, ready,
                          validate_root, validate_handoff, diagnostics as image_diagnostics,
                          stop_diagnostics, DIAGNOSTICS_EXECUTABLE)

PROTOCOL = 1
RUN = Path('/run/orchard')
CONFIG = Path('/etc/orchard/release.json')
FIRMWARE = Path('/etc/orchard/firmware.json')
PUBLIC_SUMS = Path('/etc/orchard/public.SHA256SUMS')
ROOT = Path('/run/orchard/target')


def emit(event, **values):
    print(json.dumps({'protocol': PROTOCOL, 'event': event, **values}), flush=True)


def command(*args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, **kwargs).stdout


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def config():
    return json.loads(CONFIG.read_text())


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            digest.update(block)
    return digest.hexdigest()


def state():
    path = RUN / 'state.json'
    return json.loads(path.read_text()) if path.exists() else {'phase': 'recovery'}


def begin(phase):
    marker = RUN / (phase + '.started')
    with marker.open('x') as stream:
        stream.write(str(time.time()))
    atomic(RUN / 'state.json', {'phase': phase, 'status': 'running'})
    emit('phase', phase=phase)


def finish(phase, **values):
    result = {'phase': phase, 'status': 'complete', **values}
    atomic(RUN / 'state.json', result)
    return result


def no_faults():
    text = command('dmesg').decode(errors='replace')
    if re.search(r'apple-dart.*translation fault', text):
        raise ValueError('DART fault; hardware cannot be reused in this recovery boot')
    if Path('/sys/module/nvme/parameters/j71b_ro_failed').read_text().strip() != 'N':
        raise ValueError('Storage fence failed')


def failure_state():
    marker = RUN / 'handoff-failed'
    if marker.exists():
        return {'operation': 'handoff', 'error': marker.read_text()}
    marker = RUN / 'failure.json'
    return json.loads(marker.read_text()) if marker.exists() else None


def system_ready():
    return ready(ssh_revoked=ssh_access.revoked(config()['release']))


def revoke_ssh(request):
    if (request.get('boot_id') != ssh_access.BOOT_ID.read_text().strip() or
            request.get('release') != config()['release']):
        raise ValueError('SSH retirement boot/release changed')
    if failure_state() or not system_ready():
        raise ValueError('SSH can only be revoked after verified system readiness')
    device = identity.cached()
    if (device is None or type(request.get('ecid')) is not int or
            any(request.get(key) != device[key] for key in ('profile', 'ecid', 'boot_id'))):
        raise ValueError('SSH retirement identity differs from this device/boot')
    handoff = json.loads((RUN / 'handoff.json').read_text())
    installed = json.loads(Path('/.orchard-install.json').read_text())
    if installed != handoff['receipt'] or installed['release'] != config()['release']:
        raise ValueError('SSH retirement requires the completed installed-root handoff')
    result = ssh_access.revoke(config()['release'], DIAGNOSTICS_EXECUTABLE, stop_diagnostics)
    return {**result, 'profile': device['profile'], 'ecid': device['ecid']}


def status(_):
    device = identity.cached()
    return {'release': config()['release'], 'profile': 'j71b', 'state': state(),
            'mode': config().get('mode', 'installer'),
            'ecid': device['ecid'] if device is not None else None,
            'boot_id': ssh_access.BOOT_ID.read_text().strip(),
            'ready': system_ready(), 'failure': failure_state(),
            'diagnostics': not ssh_access.revoked(config()['release']),
            'installation_attempted': (RUN / 'prepare.started').exists(),
            'formatting': json.loads((RUN / 'format.json').read_text()) if (RUN / 'format.json').exists() else None,
            'capabilities': ['abort']}


REBOOT_MESSAGE = 'A fresh recovery boot is required.\nPut the iPad in DFU mode, then run: orchard install'


def cleanup_policy():
    settings = config()
    receipts = list(settings.get('cleanup_receipts', []))
    if settings.get('root', {}).get('sha256'):
        receipts.append({'release': settings['release'], 'sha256': settings['root']['sha256']})
    firmware = json.loads(FIRMWARE.read_text())
    names = [row['destination'] for row in firmware['files'] + firmware['generated']]
    return receipts, names


def cleanup_mounted(partition, candidates=None):
    if (not ROOT.is_mount() or ROOT.stat().st_dev != Path(partition['path']).stat().st_rdev or
            partition_record(Path('/sys/class/block') / Path(partition['path']).name) != partition):
        raise ValueError('Cleanup target identity differs')
    no_faults()
    receipts, names = cleanup_policy()
    candidates = candidates if candidates is not None else staging.scan(ROOT, partition, receipts, names)
    mounts = [row.split()[4] for row in Path('/proc/self/mountinfo').read_text().splitlines()]
    for candidate in candidates:
        emit('progress', message='Removing an incomplete installation')
        staging.remove(ROOT, candidate, partition, receipts, names, mounts)
    if candidates:
        command('sync')
    return len(candidates)


def cleanup_leftovers():
    removed = 0
    for partition in inventory():
        if partition['filesystem'] != 'ext4':
            continue
        # A normal mount replays ext4's journal before discovery; ro,noload
        # could hide staging left by an interrupted boot. Only owned staging
        # is removed, and every partition is unmounted and fenced afterward.
        mount_target(partition, writable=True)
        try:
            removed += cleanup_mounted(partition)
        finally:
            unmount_target(partition)
    return removed


def abort(_):
    # Never clear phase guards or repeat hardware preparation in this boot.
    if (RUN / 'handoff.json').exists():
        return {'reboot_required': True, 'message': REBOOT_MESSAGE}
    if config().get('mode') == 'launcher':
        # Launch recovery never removes staging or replays journals in cleanup.
        if ROOT.is_mount():
            raise ValueError('Launch mount remains; inspect and use a fresh recovery boot')
        atomic(RUN / 'state.json', {'phase':'failed', 'reboot_required':True})
        return {'reboot_required':True, 'removed_staging':0, 'message':REBOOT_MESSAGE}
    if ROOT.is_mount():
        installed = json.loads((RUN / 'installation.json').read_text())
        partition = installed['target']
        # Only unmount/fence the positively identified mount owned by us.
        if ROOT.stat().st_dev != Path(partition['path']).stat().st_rdev:
            raise ValueError('Cleanup mount identity differs')
        try:
            removed = cleanup_mounted(partition)
        finally:
            unmount_target(partition)
    elif (RUN / 'prepare.started').exists():
        operation = RUN / 'installation.json'
        if operation.exists():
            partition = json.loads(operation.read_text())['target']
            block = Path('/sys/class/block') / Path(partition['path']).name
            mounted = {row.split()[2] for row in Path('/proc/self/mountinfo').read_text().splitlines()}
            if partition_record(block) != partition or (block / 'dev').read_text().strip() in mounted:
                raise ValueError('Cannot fence changed or externally mounted cleanup target')
            command('blockdev', '--setro', partition['path'])
        removed = cleanup_leftovers()
    else:
        removed = 0
    atomic(RUN / 'state.json', {'phase': 'failed', 'reboot_required': True, 'removed_staging': removed})
    operation = RUN / 'installation.json'
    replacement = operation.exists() and json.loads(operation.read_text()).get('replacement_started', False)
    cleanup = 'Incomplete staging removed. ' if removed else 'No recognized incomplete staging remains. '
    outcome = ('Replacement deletion was started; the old directory may be partially or fully deleted and cannot be restored.\n'
               if replacement else 'Existing directories were not deleted.\n')
    return {'reboot_required': True, 'removed_staging': removed,
            'message': cleanup + outcome + REBOOT_MESSAGE}


def bootstrap(request):
    """Serial API: own RAM key/service state; return only connection data."""
    device = identity.cached()
    if (device is None or type(request.get('ecid')) is not int or
            any(request.get(key) != device[key] for key in ('profile', 'ecid', 'boot_id'))):
        raise ValueError('SSH bootstrap identity differs from this recovery boot')
    public = request.get('public_key', '')
    if not isinstance(public, str) or not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]{60,128}', public):
        raise ValueError('Expected one Ed25519 public key')
    if not Path('/sbin/authorize-key').is_file() or not Path('/sbin/recovery-start-ssh').is_file():
        raise ValueError('SSH bootstrap is available only in RAM recovery')
    command('/sbin/authorize-key', input=(public + '\n').encode())
    directory = Path('/run/recovery')
    directory.mkdir(mode=0o700, exist_ok=True)
    pidfile = directory / 'dropbear.pid'
    running = False
    try:
        pid = int(pidfile.read_text())
        running = pid > 1 and (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')[0] == b'/usr/sbin/dropbear'
    except (OSError, ValueError):
        pass
    process = None
    if not running:
        pidfile.unlink(missing_ok=True)
        fd = os.open(directory / 'ssh.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as log:
            process = subprocess.Popen(['/sbin/recovery-start-ssh'], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 10
    while True:
        if process is not None and process.poll() is not None:
            raise RuntimeError('Recovery SSH daemon failed: ' + (directory / 'ssh.log').read_text()[-2000:])
        try:
            key = (directory / 'ssh_host_ed25519_key.pub').read_text().strip()
            if (pidfile.is_file() and
                    re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]{60,128}', key)):
                return {**device, 'host': '172.16.42.1', 'port': 22, 'host_key': key}
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Recovery SSH daemon readiness timed out')
        time.sleep(0.05)


def prepare(request):
    if request['release'] != config()['release']:
        raise ValueError('Host/device release mismatch')
    if state().get('phase') != 'recovery':
        raise ValueError('This recovery has already attempted installation')
    begin('prepare')
    length = int(request['bytes'])
    if not 0 < length <= 16 * 1024 * 1024:
        raise ValueError('Private mixin size outside limit')
    data = sys.stdin.buffer.read(length + 1)
    if len(data) != length or hashlib.sha256(data).hexdigest() != request['sha256']:
        raise ValueError('Private mixin transport integrity mismatch')
    allowed = {p['destination'] for p in json.loads(FIRMWARE.read_text())['files']}
    allowed |= {p['destination'] for p in json.loads(FIRMWARE.read_text())['generated']}
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as bundle:
        members = bundle.getmembers()
        if len(members) != len(allowed) + 1 or sum(m.size for m in members) > 32 * 1024 * 1024:
            raise ValueError('Private mixin expanded size/file count exceeds limit')
        if len({m.name for m in members}) != len(members) or any(not m.isfile() for m in members):
            raise ValueError('Private mixin contains links, duplicates or special files')
        records = {m.name: bundle.extractfile(m).read() for m in members}
    metadata = json.loads(records.pop('mixin.json'))
    live_adt = command('/sbin/capture-adt')
    if metadata['adt_sha256'] != hashlib.sha256(live_adt).hexdigest() or metadata['profile'] != 'j71b':
        raise ValueError('Private mixin is not for this recovery boot')
    if set(records) != allowed or set(metadata['files']) != allowed:
        raise ValueError('Private mixin must contain exactly firmware data')
    fixed = {p['destination']: p['sha256'] for p in json.loads(FIRMWARE.read_text())['files']}
    for name, content in records.items():
        safe_name(name)
        if name in fixed and hashlib.sha256(content).hexdigest() != fixed[name]:
            raise ValueError('Pinned firmware content differs')
        row = metadata['files'][name]
        if row != {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}:
            raise ValueError('Private firmware differs from manifest')
    # All checks precede the first firmware write, and writes are only to RAM.
    for name, content in records.items():
        path = Path('/') / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o600)
    atomic(RUN / 'mixin.json', metadata)
    for role in ('touch-start', 'touch-runtime'):
        directory = Path('/usr/libexec/orchard-j71b') / role
        for name in ('t8010-smartio.bin', 'j71b-fwstart.z2fw'):
            (directory / 'firmware').mkdir(exist_ok=True)
            shutil.copyfile('/lib/firmware/apple/' + name, directory / 'firmware' / name)
        # Public hash list is baked; append only private firmware hashes.
        text = (directory / 'PUBLIC.SHA256SUMS').read_text()
        text += ''.join(sha(directory / 'firmware' / name) + '  firmware/' + name + '\n'
                        for name in ('t8010-smartio.bin', 'j71b-fwstart.z2fw'))
        (directory / 'SHA256SUMS').write_text(text)
    process = subprocess.Popen(['/usr/libexec/orchard-j71b/hardware'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        emit('progress', message=line.rstrip())
    if process.wait():
        raise RuntimeError('Hardware preparation failed')
    emit('phase', phase='cleanup')
    removed = 0 if config().get('mode') == 'launcher' else cleanup_leftovers()
    return finish('prepare', mixin_sha256=request['sha256'], removed_staging=removed)


def inventory():
    no_faults()
    if (RUN / 'format.started').exists():
        receipt = RUN / 'format.json'
        if not receipt.exists() or json.loads(receipt.read_text()).get('status') != 'complete':
            raise ValueError('Formatting outcome is unverified; do not retry. ' + REBOOT_MESSAGE)
    return storage.inventory()


def partition_record(block):
    return storage.record(block)


def targets(_):
    if not (RUN / 'prepare.started').exists() or state().get('phase') not in ('prepare', 'install'):
        raise ValueError('Prepare hardware first')
    rows = inventory()
    token = uuid.uuid4().hex
    atomic(RUN / 'targets.json', {'token': token, 'partitions': rows})
    partitions = []
    for row in rows:
        public = {k: row[k] for k in ('id', 'filesystem', 'filesystem_uuid', 'bytes', 'label')}
        if row['filesystem'] == 'ext4':
            mount_target(row)
            try:
                space = os.statvfs(ROOT)
                public['free_bytes'] = space.f_bavail * space.f_frsize
            finally:
                unmount_target(row)
        partitions.append(public)
    return {'selection_token': token, 'boot_id': ssh_access.BOOT_ID.read_text().strip(),
            'needed_bytes': capacity_needed(config()['root']), 'partitions': partitions}


def selected(request):
    snapshot = json.loads((RUN / 'targets.json').read_text())
    if snapshot['token'] != request['selection_token']:
        raise ValueError('Target selection expired')
    matches = [p for p in snapshot['partitions'] if p['id'] == request['target']]
    if len(matches) != 1 or matches[0] not in inventory():
        raise ValueError('Selected target changed')
    return matches[0]


def format_target(request):
    boot_id = ssh_access.BOOT_ID.read_text().strip()
    operation_id = request.get('operation_id', '')
    current = state()
    if (not (RUN / 'prepare.started').exists() or
            current.get('phase') != 'prepare' or current.get('status') != 'complete' or
            (RUN / 'install.started').exists() or failure_state() or
            request.get('boot_id') != boot_id or not re.fullmatch('[0-9a-f]{32}', operation_id)):
        raise ValueError('Formatting requires completed preparation in the current recovery boot')
    if (RUN / 'format.started').exists():
        raise ValueError('Formatting was already attempted; do not retry. ' + REBOOT_MESSAGE)
    partition = selected(request)
    if (partition['filesystem'] != 'unformatted' or
            request.get('confirmation') != 'format ' + partition['id']):
        raise ValueError('Only explicitly confirmed Linux-data partitions without signatures may be formatted')
    if partition['bytes'] < capacity_needed(config()['root']):
        raise ValueError('Partition is too small for this image; nothing was formatted')
    receipt = {'operation_id': operation_id, 'boot_id': boot_id, 'target': partition['id'],
               'filesystem_uuid': str(uuid.uuid4()), 'status': 'unknown'}
    # Consume the boot-scoped permission before any command can enable writes.
    with (RUN / 'format.started').open('x') as marker:
        json.dump(receipt, marker)
        marker.flush()
        os.fsync(marker.fileno())
    atomic(RUN / 'format.json', {**receipt, 'partition': partition})
    try:
        no_faults()
        formatted = storage.format_ext4(partition, receipt['filesystem_uuid'])
        no_faults()
        result = {**receipt, 'status': 'complete', 'filesystem': formatted['filesystem']}
        atomic(RUN / 'format.json', result)
        (RUN / 'targets.json').unlink()
        return result
    except BaseException as error:
        atomic(RUN / 'format.json', {**receipt, 'partition': partition, 'error': str(error)})
        raise


def root_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', value):
        raise ValueError('Root directory must be one simple relative directory name')
    return value


def mount_target(partition, writable=False):
    if partition['filesystem'] != 'ext4':
        raise ValueError('Target must have a verified ext4 filesystem')
    ROOT.mkdir(exist_ok=True)
    if writable:
        command('blockdev', '--setrw', partition['path'])
    command('mount', '-t', 'ext4', '-o', 'rw,noatime' if writable else 'ro,noload', partition['path'], str(ROOT))


def unmount_target(partition):
    command('umount', str(ROOT))
    command('blockdev', '--setro', partition['path'])


def capacity_needed(image):
    # Conservative quick estimate: payload + 20% allocation overhead + 1 GiB.
    return (image['expanded_bytes'] * 6 + 4) // 5 + 1024**3


def directory_usage(path):
    """Allocated bytes/inodes without following links or crossing filesystems."""
    if not path.exists():
        return 0, 0
    device_id = path.stat().st_dev
    seen = set()
    allocated = inodes = 0
    def visit(entry):
        nonlocal allocated, inodes
        info = entry.lstat()
        if info.st_dev != device_id or entry.is_mount():
            raise ValueError('Destination contains a mount; refusing replacement')
        key = (info.st_dev, info.st_ino)
        if key in seen:
            return
        seen.add(key)
        # Shared hard links do not become free when this directory is deleted.
        if entry.is_dir() or info.st_nlink == 1:
            allocated += info.st_blocks * 512
            inodes += 1
        if not entry.is_symlink() and entry.is_dir():
            for child in entry.iterdir():
                visit(child)
    visit(path)
    return allocated, inodes


def destination(request):
    partition = selected(request)
    name = root_name(request['directory'])
    mount_target(partition)
    try:
        path = ROOT / name
        if path.is_symlink():
            raise ValueError('Destination is a symlink')
        exists = path.exists()
        if exists and not path.is_dir():
            raise ValueError('Destination exists but is not a directory')
        identity = None
        if exists:
            info = path.stat()
            identity = [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns]
        reclaimable, reclaimed_inodes = directory_usage(path)
        space = os.statvfs(ROOT)
        image = config()['root']
        needed_inodes = image.get('members', 65536) + 1024
        return {'directory': name, 'exists': exists, 'identity': identity,
                'free_bytes': space.f_bavail * space.f_frsize,
                'reclaimable_bytes': reclaimable, 'needed_bytes': capacity_needed(image),
                'inodes_sufficient': space.f_favail + reclaimed_inodes >= needed_inodes}
    finally:
        unmount_target(partition)


def install(request):
    if state().get('phase') != 'prepare' or state().get('status') != 'complete':
        raise ValueError('Hardware preparation is not complete')
    partition = selected(request)
    name = root_name(request['directory'])
    release = config()
    image = release['root']
    if request['sha256'] != image['sha256'] or request['bytes'] != image['bytes']:
        raise ValueError('Root stream differs from this recovery release')
    existing = destination(request)
    if existing['identity'] != request.get('destination_identity'):
        raise ValueError('Destination changed since confirmation')
    if existing['exists'] and request.get('replace') is not True:
        raise ValueError('Replacement was not explicitly confirmed')
    mount_target(partition)
    try:
        if (existing['free_bytes'] + existing['reclaimable_bytes'] < capacity_needed(image)
                or not existing['inodes_sufficient']):
            raise ValueError('Insufficient space or free inodes; nothing was deleted')
    finally:
        unmount_target(partition)
    begin('install')
    temporary = ROOT / ('.' + name + '.install-' + uuid.uuid4().hex)
    # Persist the mount identity in RAM before enabling any filesystem writes.
    atomic(RUN / 'installation.json', {'target': partition, 'directory': name,
                                       'staging': temporary.name, 'status': 'incomplete',
                                       'replacement_started': existing['exists']})
    mount_target(partition, writable=True)
    target = ROOT / name
    if target.is_symlink():
        raise ValueError('Destination became a symlink during installation')
    if existing['exists']:
        info = target.stat()
        if [info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns] != existing['identity']:
            raise ValueError('Destination changed before deletion')
        directory_usage(target)  # Reject nested mounts immediately before deletion.
        emit('phase', phase='deleting-destination')
        shutil.rmtree(target)
        command('sync')
    elif target.exists():
        raise ValueError('Destination appeared before installation')
    space = os.statvfs(ROOT)
    if (space.f_bavail * space.f_frsize < capacity_needed(image)
            or space.f_favail < image.get('members', 65536) + 1024):
        raise ValueError('Insufficient space or free inodes after deletion; replacement cannot be restored')
    temporary.mkdir(mode=0o700)
    atomic(temporary / staging.MARKER, {'schema': 1, 'profile': 'j71b', 'status': 'incomplete',
           'partition': partition['id'], 'filesystem_uuid': partition['filesystem_uuid'],
           'directory': name, 'release': release['release'], 'root_sha256': image['sha256']})
    # Archive members cannot overwrite either ownership or operation state.
    started = time.monotonic()
    emit('phase', phase='stream-extraction')
    result = extract(sys.stdin.buffer, temporary, size=image['bytes'], sha256=image['sha256'],
                     expanded_limit=image['expanded_bytes'], compression=image['compression'])
    emit('phase', phase='finalize')
    if (temporary / 'lib').readlink().as_posix() != 'usr/lib':
        raise ValueError('Root library layout differs')
    validate_root(temporary, release)
    verify_privileges(temporary)
    verify_session_access(temporary)
    mixin = json.loads((RUN / 'mixin.json').read_text())
    for firmware_name, expected in mixin['files'].items():
        # The mixin contains ONLY lib/firmware/...; never copy runtime or modules.
        # Keep the confirmed root directory separate from firmware member names.
        relative = 'usr/' + firmware_name
        root_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
        try:
            parent, leaf = parent_fd(root_fd, relative)
        finally:
            os.close(root_fd)
        try:
            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            with (Path('/') / firmware_name).open('rb') as source, os.fdopen(fd, 'wb') as target:
                shutil.copyfileobj(source, target)
        finally:
            os.close(parent)
        if sha(temporary / relative) != expected['sha256']:
            raise ValueError('Installed private firmware differs')
    # Copy fixed public integrity lists, preserving the same check at boot.
    sums = PUBLIC_SUMS.read_text()
    sums += ''.join(row['sha256'] + '  /usr/' + name + '\n' for name, row in mixin['files'].items())
    directory_fd = os.open(temporary, os.O_RDONLY | os.O_DIRECTORY)
    try:
        parent, leaf = parent_fd(directory_fd, 'usr/share/ipadlinux/hardware.SHA256SUMS')
        try:
            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=parent)
            with os.fdopen(fd, 'w') as output:
                output.write(sums)
        finally:
            os.close(parent)
    finally:
        os.close(directory_fd)
    receipt = {'schema': 1, 'release': release['release'], 'root_sha256': image['sha256'],
               'partition': partition['id'], 'filesystem_uuid': partition['filesystem_uuid'],
               'directory': name, 'completed_at': time.time()}
    atomic(temporary / '.orchard-install.json', receipt)
    no_faults()
    target = ROOT / name
    if target.is_symlink():
        raise ValueError('Destination became a symlink during installation')
    # Never overwrite a directory that appeared while streaming.
    if target.exists():
        raise ValueError('Destination changed during installation')
    (temporary / staging.MARKER).unlink()
    temporary.chmod(0o755)
    temporary.rename(target)
    atomic(target / '.orchard-install.json', receipt)
    command('sync')
    unmount_target(partition)
    atomic(RUN / 'installation.json', {'target': partition, 'directory': name,
                                       'status': 'complete', 'receipt': receipt})
    return finish('install', receipt=receipt, stream_extraction_seconds=time.monotonic() - started, **result)


def handoff(_):
    installed = json.loads((RUN / 'installation.json').read_text())
    if installed['status'] != 'complete' or state().get('phase') != 'install':
        raise ValueError('No completed installation to hand off')
    # One device-owned operation rechecks storage identity and required modes.
    partition = installed['target']
    if partition not in inventory():
        raise ValueError('Installed target changed')
    mount_target(partition)
    try:
        root = ROOT / root_name(installed['directory'])
        if root.is_symlink() or json.loads((root / '.orchard-install.json').read_text()) != installed['receipt']:
            raise ValueError('Installed receipt differs')
        validate_handoff(root)
    finally:
        unmount_target(partition)
    begin('handoff')
    # Preserve only the current ephemeral public key and host key in /run.
    shutil.copyfile('/root/.ssh/authorized_keys', '/run/recovery/authorized_keys')
    atomic(RUN / 'handoff.json', installed)
    return {'requested': True}


def diagnostics(_):
    logs = {}
    for args in (('dmesg',), ('journalctl', '-b', '-n', '120', '--no-pager')):
        try:
            logs[args[0]] = command(*args, timeout=10).decode(errors='replace')[-24000:]
        except (OSError, subprocess.SubprocessError):
            pass
    logs.update(image_diagnostics())
    # Read-only input inventory/counters help separate a driver stall from a
    # compositor/keyboard grab. Never open/grab/reset input devices here.
    for name, source in (('input_devices', '/proc/bus/input/devices'), ('interrupts', '/proc/interrupts')):
        try:
            logs[name] = Path(source).read_text()[:24000]
        except OSError:
            pass
    installation = RUN / 'installation.json'
    return {'state': state(), 'failure': failure_state(), 'ready': system_ready(), 'logs': logs,
            'installation': json.loads(installation.read_text()) if installation.exists() else None}


def installations(request):
    import launch
    return launch.installations(request)


def launch_installed(request):
    import launch
    return launch.launch(request)


OPERATIONS = {'installations': installations, 'launch': launch_installed,
              'status': status, 'prepare': prepare, 'targets': targets,
              'destination': destination, 'install': install, 'handoff': handoff,
              'diagnostics': diagnostics, 'abort': abort, 'bootstrap': bootstrap,
              'revoke-ssh': revoke_ssh, 'format-target': format_target}


def main():
    RUN.mkdir(mode=0o700, exist_ok=True)
    try:
        operation = sys.argv[1]
        request = json.loads(base64.urlsafe_b64decode(sys.argv[2])) if len(sys.argv) == 3 else {}
        if request.pop('protocol', PROTOCOL) != PROTOCOL:
            raise ValueError('Incompatible device protocol')
        if operation not in OPERATIONS:
            raise ValueError('Unknown device operation')
        if config().get('mode') == 'launcher' and operation not in (
                'status', 'diagnostics', 'bootstrap', 'prepare', 'installations', 'launch', 'abort'):
            raise ValueError('Shared launcher refuses installation/formatting operations')
        with (RUN / 'operation.lock').open('a') as lock:
            if operation not in ('status', 'diagnostics'):
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    raise RuntimeError('Operation already running') from error
            # Only an operation that acquired its lock may mark failure or
            # clean up. Keep that same ownership until failure handling ends.
            try:
                result = OPERATIONS[operation](request)
            except Exception as error:
                if operation in ('prepare', 'install', 'handoff', 'launch'):
                    atomic(RUN / 'failure.json', {'operation': operation, 'error': str(error), 'state': state()})
                    try:
                        message = abort({})['message']
                    except Exception as cleanup_error:
                        message = 'Cleanup incomplete: ' + str(cleanup_error) + '\n' + REBOOT_MESSAGE
                    raise RuntimeError(str(error) + '\n' + message) from error
                raise
        emit('result', operation=operation, result=result)
    except Exception as error:
        emit('error', message=str(error), retry=False)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

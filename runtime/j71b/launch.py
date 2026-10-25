"""Read-only installed-root discovery and one-shot launch authorization.

Compatibility is data pinned into the launcher by its producer. Never execute
Python/shell policy found on disk in recovery, or infer an init from filenames.
"""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import uuid

CATALOGUE = Path('/etc/orchard/launch.json')
MIXIN = Path('/run/orchard/mixin.json')


def regular(root, relative):
    path = root
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith('/') or any(p in ('.', '..') for p in parts):
        raise ValueError('Unsafe compatibility path')
    owner = path.lstat()
    if owner.st_uid != 0 or owner.st_mode & 0o022:
        raise ValueError('Unprotected installation root')
    for part in parts:
        path /= part
        if path.is_symlink():
            raise ValueError('Linked launch integrity path: ' + relative)
        info = path.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Unprotected launch integrity path: ' + relative)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('Unprotected launch integrity file: ' + relative)
    return path


def read_json(root, relative):
    path = regular(root, relative)
    if path.stat().st_size > 1024 * 1024:
        raise ValueError('Oversized installation metadata')
    return json.loads(path.read_text())


def sha(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            checksum.update(block)
    return checksum.hexdigest()


def init_target(root, command):
    # Resolve absolute links relative to the installed root, NEVER host /usr.
    pending = list(PurePosixPath(command).parts[1:])
    parts = []
    followed = 0
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            if not parts:
                raise ValueError('Init link escapes root')
            parts.pop()
            continue
        candidate = root.joinpath(*parts, part)
        if candidate.is_symlink():
            followed += 1
            if followed > 16:
                raise ValueError('Init link cycle')
            link = candidate.readlink()
            if link.is_absolute():
                parts = []
            pending = list(link.parts[1:] if link.is_absolute() else link.parts) + pending
        else:
            parts.append(part)
    return '/'.join(parts)


def validate(root, partition=None):
    if root.is_symlink() or not root.is_dir():
        raise ValueError('Launch root must be a real directory')
    receipt = read_json(root, '.orchard-install.json')
    records = json.loads(CATALOGUE.read_text())['installations']
    matches = [r for r in records if r['release'] == receipt.get('release') and
               r['root_sha256'] == receipt.get('root_sha256')]
    if len(matches) != 1 or receipt.get('schema') != 1:
        raise ValueError('Installation has no explicit compatible launch record')
    record = matches[0]
    if receipt.get('directory') != root.name:
        raise ValueError('Installation directory identity differs')
    if partition is not None and (receipt.get('partition') != partition['id'] or
                                  receipt.get('filesystem_uuid') != partition['filesystem_uuid']):
        raise ValueError('Installation partition/filesystem identity differs')
    if os.uname().release != record['kernel_release']:
        raise ValueError('Installed root and running kernel differ')
    for relative, expected in record['files'].items():
        if sha(regular(root, relative)) != expected:
            raise ValueError('Installed boot integrity differs: ' + relative)
    config = read_json(root, 'etc/orchard/release.json')
    if config['release'] != receipt['release'] or config['kernel_release'] != record['kernel_release']:
        raise ValueError('Installed release configuration differs')
    # Installed firmware is device-owned data, not merely distro compatibility.
    # Compare it to THIS boot's freshly ADT-bound, verified RAM preparation.
    mixin = json.loads(MIXIN.read_text())
    expected_sums = regular(root, 'etc/orchard/public.SHA256SUMS').read_text()
    for name, expected in mixin['files'].items():
        relative = 'usr/' + name
        path = regular(root, relative)
        if path.stat().st_size != expected['bytes'] or sha(path) != expected['sha256']:
            raise ValueError('Installed private firmware differs from current device: ' + name)
        expected_sums += expected['sha256'] + '  /' + relative + '\n'
    if regular(root, 'usr/share/ipadlinux/hardware.SHA256SUMS').read_text() != expected_sums:
        raise ValueError('Installed hardware integrity ledger differs')
    if init_target(root, record['init']) != record['init_target']:
        raise ValueError('Installed init link identity differs')
    if not os.access(regular(root, record['init_target']), os.X_OK):
        raise ValueError('Installed init is not executable')
    return receipt, record['init']


def installations(_):
    import device as d
    if d.config().get('mode') != 'launcher' or d.state().get('phase') != 'prepare' or d.state().get('status') != 'complete':
        raise ValueError('Launch discovery requires prepared shared recovery')
    rows = d.inventory()
    token = uuid.uuid4().hex
    d.atomic(d.RUN / 'targets.json', {'token': token, 'partitions': rows})
    found, rejected = [], []
    for partition in rows:
        if partition['filesystem'] != 'ext4':
            continue
        d.mount_target(partition)
        try:
            candidates = sorted(d.ROOT.iterdir())
            if len(candidates) > 4096:
                raise ValueError('Too many root-level entries')
            for root in candidates:
                if root.is_symlink() or not root.is_dir() or not (root / '.orchard-install.json').exists():
                    continue
                try:
                    d.root_name(root.name)
                    receipt, init = validate(root, partition)
                    found.append({'target':partition['id'], 'directory':root.name,
                                  'receipt':receipt, 'init':init})
                except (ValueError, OSError, KeyError, TypeError) as error:
                    rejected.append({'target':partition['id'], 'directory':root.name,'error':str(error)})
        finally:
            d.unmount_target(partition)
    snapshot = {'selection_token':token,'boot_id':d.ssh_access.BOOT_ID.read_text().strip(),
                'installations':found, 'rejected':rejected}
    d.atomic(d.RUN / 'launch-targets.json', snapshot)
    return snapshot


def launch(request):
    import device as d
    if d.config().get('mode') != 'launcher' or d.state().get('phase') != 'prepare' or d.state().get('status') != 'complete':
        raise ValueError('Launch requires prepared shared recovery')
    snapshot = json.loads((d.RUN / 'launch-targets.json').read_text())
    if request.get('boot_id') != snapshot['boot_id'] or request.get('selection_token') != snapshot['selection_token']:
        raise ValueError('Launch selection expired')
    matches = [r for r in snapshot['installations'] if r['target'] == request.get('target') and
               r['directory'] == request.get('directory')]
    if len(matches) != 1 or request.get('confirmation') != 'launch ' + matches[0]['directory']:
        raise ValueError('Launch was not explicitly confirmed')
    partition = d.selected(request)
    d.mount_target(partition)
    try:
        receipt, init = validate(d.ROOT / d.root_name(request['directory']), partition)
        if receipt != matches[0]['receipt'] or init != matches[0]['init']:
            raise ValueError('Installation changed since discovery')
    finally:
        d.unmount_target(partition)
    d.no_faults()
    d.begin('launch')
    installed = {'target':partition,'directory':request['directory'],'status':'complete',
                 'receipt':receipt,'launch':True,'init':init}
    d.atomic(d.RUN / 'installation.json', installed)
    d.shutil.copyfile('/root/.ssh/authorized_keys','/run/recovery/authorized_keys')
    d.atomic(d.RUN / 'handoff.json', installed)
    return {'requested':True,'receipt':receipt}

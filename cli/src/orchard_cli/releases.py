"""Resolve distro, device and boot-method manifests into workflow inputs."""
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
import fcntl
from importlib.metadata import version
import json
import os
from pathlib import Path
import tempfile
import tomllib

from orchard_hardware import device as hardware_device
from .cache import cache_root
from .downloads import download
from .http import open_url
from .manifests import ManifestError, check_source, local_file, validate, verify_file

MAX_MANIFEST = 1024 * 1024


def default_manifest(profile):
    return cache_root() / 'public' / profile / 'install.toml'


def catalogue():
    path = cache_root() / 'distros.toml'
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > MAX_MANIFEST:
        raise ValueError('Invalid distro catalogue')
    document = tomllib.loads(path.read_text())
    rows = document.get('distros', {})
    if set(document) != {'distros'} or not isinstance(rows, dict) or not all(
            isinstance(key, str) and isinstance(value, str) for key, value in rows.items()):
        raise ValueError('Expected [distros] name-to-index entries')
    return rows


def register(name, source):
    import re
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', name):
        raise ValueError('Invalid distro name')
    if str(source).startswith('https://'):
        check_source({'url': str(source)}, 'index')
        location = str(source)
    else:
        path = Path(source).expanduser().resolve(strict=True)
        if not path.is_file():
            raise ValueError('Expected distro index file')
        location = str(path)
    rows = catalogue()
    rows[name] = location
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / 'distros.toml'
    if path.is_symlink():
        raise ValueError('Linked catalogue refused')
    with tempfile.NamedTemporaryFile(mode='w', dir=root, prefix='.distros-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write('[distros]\n')
            for key, value in sorted(rows.items()):
                stream.write(json.dumps(key) + ' = ' + json.dumps(value) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'name': name, 'index': location}


def materialize(receipt, bundle, progress=None, clock=None, *, label='Manifest'):
    check_source(receipt, 'asset')
    if 'file' in receipt:
        if bundle is None:
            raise ValueError('Remote manifests must use exact URLs, not host-local file references')
        return verify_file(receipt, bundle)
    folder = cache_root() / 'artifacts/sha256'
    folder.mkdir(parents=True, exist_ok=True)
    if any(parent.is_symlink() for parent in (folder, *folder.parents)):
        raise ValueError('Linked artifact cache refused')
    target = folder / receipt['sha256']
    local_receipt = {**receipt, 'file': target.name}
    local_receipt.pop('url')
    if target.exists() or target.is_symlink():
        return verify_file(local_receipt, folder)
    task = progress.task(label) if progress else None
    with target.with_suffix('.asset.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if target.exists() or target.is_symlink():
            return verify_file(local_receipt, folder)
        partial = target.with_suffix('.download')
        if task:
            task.phase('Downloading')
            task.reset_transfer()
        with (clock.exclude('download') if clock else nullcontext()):
            download(receipt['url'], receipt['bytes'], partial,
                     progress=(lambda row: task.transfer(row['completed_bytes'], row['total_bytes'])) if task else None)
        verify_file({**local_receipt, 'file': partial.name}, folder)
        if target.is_symlink():
            raise ValueError('Linked artifact cache refused')
        os.replace(partial, target)
        if task:
            task.phase('Verified')
        return target


def read_document(source, progress=None, clock=None):
    source = str(source)
    if source.startswith('https://'):
        with (clock.exclude('download') if clock else nullcontext()):
            with open_url(source) as response:
                data = response.read(MAX_MANIFEST + 1)
        bundle = None
    else:
        path = Path(source).expanduser().absolute()
        if path.is_symlink() or path.stat().st_size > MAX_MANIFEST:
            raise ValueError('Manifest is linked or too large')
        path = local_file(path.name, path.parent)
        data = path.read_bytes()
        bundle = path.parent
    if len(data) > MAX_MANIFEST:
        raise ValueError('Manifest exceeds size limit')
    return validate(tomllib.loads(data.decode('utf-8'))), bundle


def select_manifest(source, profile, progress=None, clock=None, purpose='install'):
    expected_distro = None
    if isinstance(source, str) and not source.startswith('https://'):
        # A registered bare name wins over a same-named local directory. Use
        # ./name (or a Path) when explicitly selecting a local manifest.
        location = catalogue().get(source) if '/' not in source and '\\' not in source else None
        if location:
            expected_distro = source
            source = location
        elif not Path(source).expanduser().exists():
            raise ValueError('Unknown distro or manifest. Register an index with: orchard distros NAME INDEX_URL')
    document, bundle = read_document(source, progress, clock)
    if document['kind'] == 'orchard.index':
        if expected_distro and document['distro'] != expected_distro:
            raise ValueError('Distro index identity differs from its registered name')
        methods = document['devices'].get(profile, {})
        supported = hardware_device(profile)['boot']['methods']
        matches = [method for method in methods if method in supported]
        if len(matches) != 1:
            raise ValueError('No unique supported boot method for this distro/device')
        method = matches[0]
        receipt = methods[method]
        if receipt['bytes'] > MAX_MANIFEST:
            raise ValueError('Installation manifest exceeds size limit')
        path = materialize(receipt, bundle, None, clock)
        selected, local_bundle = read_document(path, progress, clock)
        if (selected['kind'] != 'orchard.install' or selected['distro'] != document['distro'] or
                selected['device'] != profile or selected['boot_method'] != method):
            raise ValueError('Selected manifest does not match index distro/device/method')
        document = selected
        bundle = local_bundle if 'file' in receipt else None
    elif expected_distro and document.get('distro') != expected_distro:
        raise ValueError('Registered distro does not match manifest')
    allowed = {'install': ('orchard.install',)}.get(purpose, ('orchard.install', 'orchard.boot'))
    if document['kind'] not in allowed or document['device'] != profile:
        raise ValueError('Manifest is not compatible with this device/operation')
    if document['boot_method'] != 'checkm8-pongo':
        raise ValueError('This Orchard build implements only checkm8-pongo')
    if document['protocol'] != 1:
        raise ValueError('Unsupported device protocol; this manifest is for another Orchard version')
    actual = tuple(int(part) for part in version('orchard-cli').split('.'))
    minimum = tuple(int(part) for part in document['min_orchard'].split('.'))
    if actual < minimum:
        raise ValueError('Upgrade Orchard to at least ' + document['min_orchard'])
    if purpose == 'install' and document['install']['default_root'] == '/':
        raise ValueError('Filesystem-root installation is not implemented; use a directory-root image')
    return document, bundle


def resolve(manifest, profile, progress=None, clock=None, purpose='install'):
    document, bundle = select_manifest(manifest, profile, progress, clock, purpose)
    roles = {'pongo': 'pongo', 'recovery': 'boot'}
    if purpose == 'install':
        roles['root'] = 'root'
    paths, receipts = {}, {role: document[field] for role, field in roles.items()}
    labels = {'pongo': 'Pongo', 'recovery': 'Recovery', 'root': document.get('name', 'Linux')}
    with ThreadPoolExecutor(max_workers=len(roles)) as pool:
        futures = {role: pool.submit(materialize, receipt, bundle, progress, clock, label=labels[role])
                   for role, receipt in receipts.items()}
        for role, future in futures.items():
            paths[role] = future.result()
    record = {'protocol': document['protocol'], 'profile': profile, 'assets': receipts,
              'release': document.get('version'), 'name': document.get('name', 'Linux'),
              'default_root': document.get('install', {}).get('default_root', '/linux')}
    return record, paths

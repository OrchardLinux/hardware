"""Verified private firmware cache and data-only mixin packaging, for any profile."""
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import tarfile

from .cache import cache_root
from .integrity import digest
from . import recipe as recipes


def missing_inputs(locations, expected):
    return [name for name, path in locations.items()
            if path.is_symlink() or not path.is_file() or digest(path) != expected[name]]


def store_input(path, data, checksum):
    if hashlib.sha256(data).hexdigest() != checksum:
        raise ValueError('Extracted firmware differs: ' + path.name)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Linked firmware cache entry refused')
    path.write_bytes(data)
    path.chmod(0o600)


def pack_mixin(prepared, metadata, outdir):
    """Package the caller's identity metadata without interpreting its hardware format."""
    if prepared.is_symlink() or (prepared / 'firmware').is_symlink():
        raise ValueError('Linked private firmware root refused')
    entries = {}
    for name in metadata['files']:
        if (not isinstance(name, str) or not name.startswith('lib/firmware/') or
                '\\' in name or any(part in ('', '.', '..') for part in name.split('/'))):
            raise ValueError('Private mixin may contain only normalized firmware data paths')
        path = prepared / 'firmware'
        for part in name.split('/'):
            path = path / part
            if path.is_symlink():
                raise ValueError('Linked private firmware refused')
        entries[name] = path.read_bytes()
    entries['mixin.json'] = json.dumps(metadata, sort_keys=True).encode()
    archive = outdir / 'mixin.tar.gz'
    with tarfile.open(archive, 'w:gz') as bundle:
        for name, data in sorted(entries.items()):
            if name != 'mixin.json' and not name.startswith('lib/firmware/'):
                raise ValueError('Private mixin may contain only firmware data')
            member = tarfile.TarInfo(name)
            member.size, member.mode = len(data), 0o600
            bundle.addfile(member, io.BytesIO(data))
    archive.chmod(0o600)
    record = {'schema': 1, 'kind': 'private-mixin', 'bytes': archive.stat().st_size,
              'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    (outdir / 'mixin.json').write_text(json.dumps(record, indent=2) + '\n')
    (outdir / 'mixin.json').chmod(0o600)
    return archive, record


def recipe_for(profile, hardware=None):
    """A device's firmware recipe, validated and supported.

    From the installed hardware package, or from the hardware checkout at
    HARDWARE (a development build against another branch).
    """
    if hardware is None:
        from orchard_hardware import firmware_path
        return recipes.check(json.loads(firmware_path(profile).read_text()))
    from .build import tomllib
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', profile):
        raise ValueError('Invalid device profile')
    descriptor = Path(hardware) / 'devices' / profile / 'device.toml'
    with descriptor.open('rb') as stream:
        name = tomllib.load(stream)['firmware']['recipe']
    parts = name.split('/')
    if '\\' in name or ':' in name or any(part in ('', '.', '..') for part in parts):
        raise ValueError('Unsafe firmware recipe resource')
    path = descriptor.parent.joinpath(*parts)
    if any(p.is_symlink() for p in (path, *path.parents) if descriptor.parent in (p, *p.parents)):
        raise ValueError('Firmware recipe path contains a symlink')
    return recipes.check(json.loads(path.read_text()))


def ensure(profile, clock, progress, *, allow_download=True, hardware=None):
    """Every firmware output of the device's recipe, verified and cached by hash.

    Only missing outputs are produced, from only the IPSW inputs they need,
    read from Apple's server by byte range (apple/remote_ipsw.py).
    Returns the directory holding the outputs, named by their sha256.
    """
    recipe = recipe_for(profile, hardware)
    root = cache_root() / 'firmware' / profile
    outputs = root / 'outputs'
    outputs.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    expected = {o['sha256']: o['sha256'] for o in recipe['outputs']}
    absent = set(missing_inputs({sha: outputs / sha for sha in expected}, expected))
    missing = [o for o in recipe['outputs'] if o['sha256'] in absent]
    if not missing:
        progress.phase('Verified cached firmware')
        return outputs
    if not allow_download:
        raise ValueError('Verified private firmware cache is missing. Launch does not download firmware by default; '
                         'explicitly use --allow-firmware-download to acquire it.')
    from .apple.remote_ipsw import Ipsw
    source = recipe['ipsw']
    task = progress.task('Apple firmware')
    task.phase('Downloading only the needed parts')
    task.reset_transfer()
    with clock.exclude('download'):
        ipsw = Ipsw(source['url'], source['bytes'], on_bytes=lambda sent: task.transfer(sent, None))

        def read_input(name):
            location = recipe['inputs'][name]['ipsw']
            if 'zip' in location:
                return ipsw.member(location['zip'])
            if 'kernelcache' in location:
                return ipsw.kernelcache(profile + 'ap')
            return ipsw.system_file(location['rootfs'])

        for destination, data in recipes.run(recipe, read_input, missing).items():
            checksum = next(o['sha256'] for o in missing if o['destination'] == destination)
            store_input(outputs / checksum, data, checksum)
    task.phase('Verified')
    return outputs


def mixin(profile, firmware_dir, outdir):
    """Stage the recipe's outputs under their destinations and pack the data-only mixin."""
    recipe = recipe_for(profile)
    if outdir.exists():
        raise ValueError('Choose a new private output directory')
    outdir.mkdir(parents=True, mode=0o700)
    prepared = outdir / 'prepared'
    files = {}
    for output in recipe['outputs']:
        source = Path(firmware_dir) / output['sha256']
        if source.is_symlink() or not source.is_file() or digest(source) != output['sha256']:
            raise ValueError('Cached firmware output is missing or differs: ' + output['destination'])
        target = prepared / 'firmware' / output['destination']
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, target)
        target.chmod(0o600)
        files[output['destination']] = {'bytes': target.stat().st_size, 'sha256': output['sha256']}
    return pack_mixin(prepared, {'schema': 1, 'profile': profile, 'files': files}, outdir)

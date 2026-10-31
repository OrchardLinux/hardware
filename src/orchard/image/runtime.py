"""Public files of the recovery and the platform layer, and the recovery's own runtime."""
import hashlib
import json
from pathlib import Path

from ..hardware import ROOT as PROJECT


def module_files(modules_dir: Path):
    """(kernel release, {path: (data, mode)}) for the one staged module tree."""
    releases = [p for p in modules_dir.iterdir() if p.is_dir() and not p.is_symlink()]
    if len(releases) != 1:
        raise ValueError('Expected one kernel module release')
    release = releases[0].name
    if (modules_dir.parent.parent / 'kernel.release').read_text().strip() != release:
        raise ValueError('Kernel module release differs')
    result = {}
    for path in sorted(releases[0].rglob('*')):
        if path.is_symlink():
            raise ValueError('Public module tree contains a symlink')
        if path.is_file():
            result['usr/lib/modules/' + release + '/' + path.relative_to(releases[0]).as_posix()] = (path.read_bytes(), 0o644)
    return release, result


def regulatory_files():
    """The public wireless regulatory database, checked against its pinned source.

    cfg80211 needs the database AND its upstream detached signature before
    Wi-Fi probing, in recovery and installed systems alike. This is not
    private device firmware.
    """
    regulatory = PROJECT / 'system/regulatory'
    source = json.loads((regulatory / 'source.json').read_text())
    result = {}
    for name, target in (('regulatory.db', 'usr/lib/firmware/regulatory.db'),
                         ('regulatory.db.p7s', 'usr/lib/firmware/regulatory.db.p7s'),
                         ('LICENSE', 'usr/share/licenses/orchard-regdb/LICENSE')):
        data = (regulatory / name).read_bytes()
        if len(data) != source['files'][name]['bytes'] or hashlib.sha256(data).hexdigest() != source['files'][name]['sha256']:
            raise ValueError('Public regulatory data differs from pinned source: ' + name)
        result[target] = (data, 0o644)
    result['usr/share/licenses/orchard-regdb/source.json'] = ((regulatory / 'source.json').read_bytes(), 0o644)
    return result


def checksums(files):
    """A SHA256SUMS listing of FILES, by absolute path."""
    return ''.join(hashlib.sha256(data).hexdigest() + '  /' + name + '\n'
                   for name, (data, _) in sorted(files.items())).encode()


def files(modules_dir: Path):
    """The recovery's kernel modules and public firmware data, with their SHA256SUMS."""
    release, result = module_files(modules_dir)
    result.update(regulatory_files())
    result['etc/orchard/public.SHA256SUMS'] = (checksums(result), 0o644)
    return release, result


def recovery_files():
    """The recovery's device runtime (runtime/recovery): orchard-device, identity, storage fence, start."""
    prefix = 'usr/libexec/orchard/'
    source = PROJECT / 'runtime/recovery'
    result = {prefix + name: ((source / name).read_bytes(), 0o755)
              for name in ('device.py', 'identity.py', 'storage-fence', 'start.py')}
    result[prefix + 'adt.py'] = ((PROJECT / 'src/orchard/apple/adt.py').read_bytes(), 0o644)
    result['usr/bin/orchard-device'] = (b'#!/bin/sh\nexec /usr/bin/python3 /usr/libexec/orchard/device.py "$@"\n', 0o755)
    return result

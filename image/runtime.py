"""Public files shared by the recovery and an installed root, and the recovery's own runtime."""
import hashlib
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def files(modules_dir: Path):
    """Kernel modules and public firmware data, with their SHA256SUMS."""
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
    # cfg80211 needs the public database AND its upstream detached signature
    # in recovery, before Wi-Fi probing. This is not private device firmware.
    regulatory = PROJECT / 'devices/j71b/public-firmware'
    source = json.loads((regulatory / 'source.json').read_text())
    for name, target in (('regulatory.db', 'usr/lib/firmware/regulatory.db'),
                         ('regulatory.db.p7s', 'usr/lib/firmware/regulatory.db.p7s'),
                         ('LICENSE', 'usr/share/licenses/orchard-regdb/LICENSE')):
        data = (regulatory / name).read_bytes()
        if len(data) != source['files'][name]['bytes'] or hashlib.sha256(data).hexdigest() != source['files'][name]['sha256']:
            raise ValueError('Public regulatory data differs from pinned source: ' + name)
        result[target] = (data, 0o644)
    result['usr/share/licenses/orchard-regdb/source.json'] = ((regulatory / 'source.json').read_bytes(), 0o644)
    sums = ''.join(hashlib.sha256(data).hexdigest() + '  /' + name + '\n'
                   for name, (data, _) in sorted(result.items()))
    result['etc/orchard/public.SHA256SUMS'] = (sums.encode(), 0o644)
    return release, result


def recovery_files():
    """The recovery's device runtime (runtime/recovery): orchard-device, identity, storage fence, start."""
    prefix = 'usr/libexec/orchard/'
    source = PROJECT / 'runtime/recovery'
    result = {prefix + name: ((source / name).read_bytes(), 0o755)
              for name in ('device.py', 'identity.py', 'storage-fence', 'start.py')}
    result[prefix + 'adt.py'] = ((PROJECT / 'apple/adt.py').read_bytes(), 0o644)
    result['usr/bin/orchard-device'] = (b'#!/bin/sh\nexec /usr/bin/python3 /usr/libexec/orchard/device.py "$@"\n', 0o755)
    return result

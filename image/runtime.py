"""Public runtime producer shared by recovery and the installed root."""
import hashlib
import json
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
TOUCH = PROJECT / 'devices/j71b/touch/runtime'
MODULES = {
    'touch-start': ('virt-dma', 'apple-sio-core', 'apple-sio-dma', 'j71b_dma_bindings',
                    'j71b_sio_denied_test', 'j71b_spi_dma_reset', 'j71b_touch_start'),
    'touch-runtime': ('virt-dma', 'apple-sio-core', 'apple-sio-dma', 'j71b_dma_bindings', 'j71b_z2'),
}


def files(modules_dir: Path, policy_dir: Path):
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
    prefix = 'usr/libexec/orchard-j71b/'
    for role, names in MODULES.items():
        entries = {}
        for path in sorted((TOUCH / role).glob('*.sh')):
            entries[path.name] = (path.read_bytes().replace(b'@KERNEL_RELEASE@', release.encode()), 0o755)
        for name in names:
            candidates = list(releases[0].rglob(name + '.ko'))
            if len(candidates) != 1:
                raise ValueError('Missing/ambiguous public touch module: ' + name)
            entries['modules/' + name + '.ko'] = (candidates[0].read_bytes(), 0o644)
        sums = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                       for name, (data, _) in sorted(entries.items()))
        entries['PUBLIC.SHA256SUMS'] = (sums.encode(), 0o644)
        result.update({prefix + role + '/' + name: value for name, value in entries.items()})
    result[prefix + 'touch-activate'] = ((TOUCH / 'touch-activate').read_bytes().replace(
        b'@KERNEL_RELEASE@', release.encode()), 0o755)
    for source, target in (('phase2-hardware', 'hardware'), ('device.py', 'device.py'),
                           ('archive.py', 'archive.py'), ('handoff.py', 'handoff.py'),
                           ('staging.py', 'staging.py'), ('ssh_access.py', 'ssh_access.py'),
                           ('storage.py', 'storage.py'), ('identity.py', 'identity.py'),
                           ('launch.py', 'launch.py')):
        result[prefix + target] = ((PROJECT / 'runtime/j71b' / source).read_bytes(), 0o755)
    result[prefix + 'adt.py'] = ((PROJECT / 'apple/adt.py').read_bytes(), 0o644)
    result['usr/bin/orchard-device'] = (b'#!/bin/sh\nexec /usr/bin/python3 /usr/libexec/orchard-j71b/device.py "$@"\n', 0o755)
    if not (policy_dir / 'image_policy.py').is_file():
        raise ValueError('Image must supply its own image_policy.py')
    for path in sorted(policy_dir.glob('*.py')):
        if path.is_symlink() or prefix + path.name in result:
            raise ValueError('Unsafe/colliding image policy file')
        result[prefix + path.name] = (path.read_bytes(), 0o644)
    for name in ('j71b-check-platform', 'j71b-charge-lease'):
        result['usr/libexec/' + name] = ((PROJECT / 'runtime/j71b' / name).read_bytes().replace(
            b'@KERNEL_RELEASE@', release.encode()), 0o755)
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

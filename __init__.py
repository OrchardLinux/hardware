"""Standalone hardware data resources; no dependency on Orchard or any distro."""
from pathlib import Path
import re
try:
    import tomllib
except ImportError:
    import tomli as tomllib


def device_path(profile):
    if not isinstance(profile, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', profile):
        raise ValueError('Invalid hardware profile')
    path = Path(__file__).parent / 'devices' / profile / 'device.toml'
    if not path.is_file() or path.is_symlink():
        raise ValueError('Hardware support is not installed for ' + profile)
    return path


def device(profile):
    return tomllib.loads(device_path(profile).read_text())


def firmware_path(profile):
    descriptor = device_path(profile)
    name = device(profile)['firmware']['recipe']
    if ('\\' in name or ':' in name or any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('Unsafe firmware recipe resource')
    path = descriptor.parent
    for part in name.split('/'):
        path = path / part
        if path.is_symlink():
            raise ValueError('Linked firmware recipe refused')
    if not path.is_file():
        raise ValueError('Firmware recipe resource is missing')
    return path

"""The hardware data of this checkout: device descriptors, features, firmware recipes, loaders."""
from pathlib import Path
import re
import tomllib

# The repository root: devices/, kernel/, boot/ and schemas/ sit beside src/.
ROOT = Path(__file__).resolve().parents[2]


def device_path(profile):
    if not isinstance(profile, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', profile):
        raise ValueError('Invalid hardware profile')
    path = ROOT / 'devices' / profile / 'device.toml'
    if not path.is_file() or path.is_symlink():
        raise ValueError('Hardware support is not installed for ' + profile)
    return path


def profiles():
    """Every device this hardware data describes."""
    root = ROOT / 'devices'
    return sorted(path.parent.name for path in root.glob('*/device.toml')
                  if not path.is_symlink() and not path.parent.is_symlink())


def features():
    """Feature names and display labels for device.toml [features], in display order."""
    return tomllib.loads((ROOT / 'features.toml').read_text())


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


def loader(profile):
    """The prebuilt Pongo and storage-capable m1n1 that start this device, if packaged.

    `orchard launch` needs only these two: m1n1 then reads the device's boot
    partition (docs/boot-contract.md), so launching depends on no distro and
    on nothing an install left on the host. Both are built once for every
    Apple device (boot/m1n1/build apple, boot/quiet/build-pongo.py); the
    published wheel carries them in loaders/, a source checkout has none and
    the CLI builds them instead. Returns {'pongo': Path, 'm1n1': Path} or None.
    """
    device_path(profile)
    root = ROOT / 'loaders'
    files = {'pongo': root / 'pongo.bin', 'm1n1': root / 'm1n1.bin'}
    if all(path.is_file() and not path.is_symlink() for path in files.values()):
        return files
    return None

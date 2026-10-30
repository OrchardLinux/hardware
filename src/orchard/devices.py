"""Device registry from the installed hardware data. Shared workflow never interprets hardware identities."""
from pathlib import Path
from typing import List, Optional, Tuple

from .contracts import Adapter, SelectedDevice


def profiles() -> List[str]:
    from . import hardware
    return hardware.profiles()


def load(profile: Optional[str] = None) -> Adapter:
    """The adapter for a profile, or for the project's configured device."""
    from . import hardware
    from .apple.device import Device
    if profile is None:
        from .build import configured_device
        profile = configured_device(Path.cwd())
        if profile is None:
            raise ValueError('No device selected: add device = "<board>" to orchard.toml')
    if profile not in profiles():
        raise ValueError('Hardware support is not installed for ' + profile)
    adapter = Device(hardware.device(profile))
    if not isinstance(adapter, Adapter):
        raise ValueError('Incomplete adapter contract for ' + profile)
    return adapter


def choose() -> Tuple[Adapter, SelectedDevice]:
    """The one connected device, matched against every described device."""
    from .apple import usb
    device = usb.sole_target()
    adapters = [load(profile) for profile in profiles()]
    if device['mode'] == 'linux':
        matches = [a for a in adapters if a.PRODUCT and device.get('product') == a.PRODUCT]
    else:
        matches = [a for a in adapters if a.matches_dfu(device)]
    if len(matches) != 1:
        raise ValueError('The connected device is not one Orchard supports '
                         f"(chip {device.get('chip')}, board {device.get('board')})"
                         if not matches else 'The connected device matches several device descriptions')
    return matches[0], matches[0].discover()

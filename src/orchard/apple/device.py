"""Generic Apple device adapter: identity from hardware's device.toml, shared transports.

Supporting a new device needs only its hardware descriptor and firmware
recipe, not CLI code.
"""
from contextlib import AbstractContextManager
from functools import partial
from pathlib import Path
from typing import Any, Dict, List, Optional, cast

from orchard.contracts import BootAssets, BootRequest, Connection, SelectedDevice
from orchard.state import BootState, DeviceState
from orchard.progress import Progress
from orchard.apple import usb, boot as apple_boot, adt_capture
from orchard.apple.adt_tree import parse
from orchard.transports import ssh
from orchard import firmware
from .adt import validate as validate_adt


# The recovery's USB product; its serial string names the board (BOARD:j71b).
RECOVERY_PRODUCT = 'Orchard Recovery'


class Device:
    def __init__(self, descriptor: Dict[str, Any]):
        identity = descriptor['device']
        self.PROFILE = identity['profile']
        self.NAME = identity['name']
        self.APPLE_PRODUCT = identity['apple_products'][0]
        # USB product of Orchard's recovery, the same on every device; absent until it has one.
        self.PRODUCT = RECOVERY_PRODUCT if descriptor['match'].get('linux', {}).get('recovery') else None
        self.CHIP = descriptor['match']['dfu']['chip']
        self.BOARD = descriptor['match']['dfu']['board']
        dtb = descriptor.get('kernel', {}).get('dtb')
        self.DEVICETREE = dtb + '.dtb' if dtb else None
        support = descriptor.get('support', {})
        self.INSTALL = bool(support.get('install', False))
        self.STATUS = support.get('status', 'experimental')
        self.CAVEATS = list(support.get('caveats', []))
        self.FEATURES = dict(descriptor.get('features', {}))
        self.REBOOT_MESSAGE = ('A fresh recovery boot is required.\n'
                               f'Put the {self.NAME} in DFU mode, then run: orchard install')
        self.firmware_inputs = partial(firmware.ensure, self.PROFILE)
        self.prepare_mixin = partial(firmware.mixin, self.PROFILE)

    def matches_dfu(self, device: Dict[str, Any]) -> bool:
        return (device.get('chip'), device.get('board')) == (self.CHIP, self.BOARD)

    def recovery_endpoint(self) -> Dict[str, Any]:
        if self.PRODUCT is None:
            raise ValueError(f'The {self.NAME} has no Orchard recovery system yet')
        return usb.recovery(self.PRODUCT, self.PROFILE)

    def connect(self, device: SelectedDevice) -> AbstractContextManager[Connection]:
        return ssh.session(DeviceState(self.PROFILE, device['ecid']), self.recovery_endpoint,
                           expected_boot=device.get('boot_id'))

    def capture_adt(self, device: Optional[SelectedDevice] = None) -> bytes:
        # Bring-up and diagnostics only; installs do not need the ADT.
        # DFU/Pongo and Linux location IDs differ: bind the fresh ADT to the ECID.
        data = adt_capture.capture(self.recovery_endpoint)
        if parse(data).properties.get('model', b'').rstrip(b'\0') != self.APPLE_PRODUCT.encode():
            raise ValueError('Captured ADT is not for ' + self.APPLE_PRODUCT)
        if device is not None and validate_adt(data) != device['ecid']:
            raise ValueError('Fresh ADT identity differs from the confirmed device')
        return data

    def list_devices(self) -> List[Dict[str, Any]]:
        return usb.devices()

    def discover(self) -> SelectedDevice:
        device = usb.sole_target()
        if device['mode'] == 'linux':
            endpoint = self.recovery_endpoint()
            BootState(DeviceState(self.PROFILE, endpoint['ecid']), endpoint['boot_id'])
            return cast(SelectedDevice, {**endpoint, 'resumed': True})
        if not self.matches_dfu(device) or not device['ecid']:
            raise ValueError(f'The connected device is not a {self.NAME}')
        DeviceState(self.PROFILE, device['ecid'])
        return cast(SelectedDevice, {**device, 'resumed': False})

    def boot(self, device: SelectedDevice, assets: BootAssets,
             manifest: BootRequest, progress: Progress) -> None:
        return apple_boot.boot(device, assets, manifest, progress,
                               profile=self.PROFILE, chip=self.CHIP, board=self.BOARD, product=self.PRODUCT)

    def start_loader(self, device: SelectedDevice, loader: Dict[str, Path], progress: Progress) -> str:
        return apple_boot.start_loader(device, loader, progress, profile=self.PROFILE,
                                       chip=self.CHIP, board=self.BOARD, product=self.PRODUCT)

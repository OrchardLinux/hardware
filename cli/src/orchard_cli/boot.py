"""Device confirmation and tethered boot of locally assembled RAM payloads.

Uses the existing USB/Pongo transport; no installer or device-side RPC required.
"""
from pathlib import Path
import shutil
import subprocess
import sys

from .ram import verify_ram


def confirm_boot(*, mismatch=False):
    if not sys.stdin.isatty():
        raise ValueError('Boot confirmation requires an interactive terminal; use --yes for non-interactive RAM boot')
    try:
        prompt = 'Try booting anyway? [y/N] ' if mismatch else 'Boot this device? [Y/n] '
        answer = input(prompt).strip().lower()
    except EOFError:
        raise ValueError('Boot cancelled: input closed') from None
    if answer not in ('y', 'yes') and not (answer == '' and not mismatch):
        raise ValueError('Boot cancelled')


def boot_ram(output, *, yes=False):
    """Boot once via existing USB/Pongo transport. No recovery API required."""
    from orchard_hardware import device as hardware_device
    from .apple import usb
    from .apple.boot import start_pongo
    from .state import operation_lock
    from .transports import pongo

    output = Path(output).resolve()
    receipt = verify_ram(output)
    try:
        description = hardware_device(receipt['device'])
    except ValueError:
        description = None
    with operation_lock():
        device = usb.sole_target()
        if device['mode'] not in ('dfu', 'pongo'):
            raise ValueError('Requires a device in DFU/Pongo; will not reboot running Linux')
        chip, board = (device.get(name) for name in ('chip', 'board'))
        mismatch = description is None or any(
            device.get(name) != description['match']['dfu'][name] for name in ('chip', 'board'))
        ecid = device.get('ecid')
        if not ecid:
            raise ValueError('Missing device ECID')
        if device['mode'] == 'dfu' and not shutil.which('palera1n'):
            raise ValueError('Install palera1n before booting')
        name = description['device']['name'] if not mismatch else device.get('product') or 'Unrecognized device'
        print(f'{name}, ECID {ecid:x}, USB {device["location"]}, mode {device["mode"]}.')
        print("Experimental RAM boot; the payload's /init determines what runs.")
        if mismatch:
            if description is not None:
                print(f'WARNING: This build was prepared for an {description["device"]["name"]}.')
                print('Your connected device is different.')
            else:
                print('WARNING: The intended device for this build is unknown.')
                print('Compatibility with your connected device has not been established.')
            print('It may fail to boot or stop responding until you restart it.')
        if not yes:
            confirm_boot(mismatch=mismatch)
        verify_ram(output)
        usb.selected(device['location'], ecid, chip=chip, board=board, mode=device['mode'])
        if device['mode'] == 'dfu':
            with (output / 'palera1n.log').open('wb') as log:
                start_pongo(device, output / 'pongo.bin', log, print,
                            chip=chip, board=board, product='', seconds=60)
        usb.selected(device['location'], ecid, chip=chip, board=board)
        pongo.upload(output / 'linux.bin', device['location'], receipt['files']['linux.bin'])
    print('Upload/boot request sent; Linux readiness has NOT been verified.')
    print('Connect to its USB serial port: screen /dev/cu.usbmodemYOUR_PORT 115200 (macOS)')
    print('or screen /dev/ttyACM0 115200 (Linux; verify the actual port).')


def run_directory(directory, *, yes=False):
    """Friendly error boundary around boot_ram; never retries a boot request."""
    try:
        boot_ram(Path(directory).expanduser().resolve() / 'out', yes=yes)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        raise SystemExit(f'{error}\nStopped. Inspect device state before any new boot attempt.')

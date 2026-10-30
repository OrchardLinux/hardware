"""Device confirmation and tethered boot of locally assembled RAM payloads.

Uses the existing USB/Pongo transport; no installer or device-side RPC required.
"""
from pathlib import Path
import json
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
    from .hardware import device as hardware_device
    from .apple import usb
    from .apple.boot import start_pongo
    from .state import operation_lock
    from .transports import pongo

    output = Path(output).resolve()
    receipt = verify_ram(output)
    descriptions = []
    for profile in receipt['boards']:
        try:
            descriptions.append(hardware_device(profile))
        except ValueError:
            pass
    with operation_lock():
        device = usb.sole_target()
        if device['mode'] not in ('dfu', 'pongo'):
            raise ValueError('Requires a device in DFU/Pongo; will not reboot running Linux')
        chip, board = (device.get(name) for name in ('chip', 'board'))
        matching = [d for d in descriptions
                    if all(device.get(name) == d['match']['dfu'][name] for name in ('chip', 'board'))]
        description = matching[0] if matching else (descriptions[0] if len(descriptions) == 1 else None)
        mismatch = not matching
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
            elif descriptions:
                print(f'WARNING: This build was prepared for {receipt.get("platform") or "other"} boards '
                      f'({", ".join(receipt["boards"])}); your connected device is not one of them.')
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


def run_directory(directory, *, yes=False, build=True, device=None, platform=None, **build_options):
    """Build what is missing or outdated for the attached board, then boot it once.

    The attached board picks the platform (--device or --platform choose one
    for a board no hardware support describes yet). With payload.toml
    firmware = true, that board's firmware is added as its own layer.
    Never retries a boot request.
    """
    from . import ram
    from .devices import attached_board
    from .hardware import ROOT
    directory = Path(directory).expanduser().resolve()
    board = device or attached_board()
    platform = ram.resolve_platform(directory, build_options.get('hardware') or ROOT,
                                    platform=platform, device=board)
    output = directory / 'out' / platform
    if build:
        output = ram.build_directory(directory, platform=platform, **build_options)
    elif not (output / 'build.json').is_file():
        raise SystemExit(f'No {platform} build in {directory}; run without --no-build')
    if json.loads((output / 'build.json').read_text()).get('firmware'):
        if board is None:
            raise SystemExit('This payload adds the booted board\'s firmware: connect a described device or pass --device')
        output = ram.with_firmware(output, board, hardware=build_options.get('hardware'))
    try:
        boot_ram(output, yes=yes)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
        raise SystemExit(f'{error}\nStopped. Inspect device state before any new boot attempt.')

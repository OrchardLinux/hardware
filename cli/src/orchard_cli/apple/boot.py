"""Shared checkm8/Pongo boot sequence; adapters supply device identity constraints."""
import os
import signal
import subprocess
import shutil
import time
import tempfile
import threading

from orchard_cli.integrity import digest
from orchard_cli.state import DeviceState, private_directory
from orchard_cli.transports import pongo
from . import replug, usb


def wait_mode(device, mode, *, chip, board, product, seconds=30):
    deadline = time.monotonic() + seconds
    observed = []
    probe_error = ''
    while True:
        try:
            observed = usb.devices()
            active = [d for d in observed if d['mode'] in ('dfu', 'pongo', 'linux')]
            if len(active) > 1:
                raise ValueError('Connect exactly one target; no boot retry was performed')
            if mode == 'linux':
                matches = [d for d in observed if d['mode'] == 'linux' and
                           d.get('product') == product and d.get('serial_port')]
            else:
                matches = [d for d in observed if d['location'] == device['location']]
            if len(matches) == 1 and matches[0]['mode'] == mode:
                if mode == 'pongo':
                    usb.selected(device['location'], device['ecid'], chip=chip, board=board)
                elif mode == 'linux':
                    if matches[0].get('ecid') != device['ecid'] or not matches[0].get('boot_id'):
                        raise ValueError('Recovery device identity differs or lacks boot identity; use the current recovery')
                return matches[0]
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            # Only USB enumeration is retried, within the existing deadline.
            # Boot/upload live outside this loop and are never replayed.
            detail = error.stderr or b''
            if isinstance(detail, bytes):
                detail = detail.decode(errors='replace')
            probe_error = f' Last USB enumeration error: {error}. {detail[-1000:]}'
        if time.monotonic() >= deadline:
            states = ', '.join(f'{d["mode"]} ({d.get("product") or "unnamed"})' for d in observed) or 'no Apple USB devices'
            raise RuntimeError(f'{mode} readiness timed out; observed: {states}.{probe_error} '
                               'Inspect USB state; no boot retry was performed.')
        time.sleep(0.25)


def _exit_on_sigterm(signum, frame):
    raise SystemExit(128 + signum)


def run_process_group(argv, *, timeout, **kwargs):
    """subprocess.run(check=True) that also stops the command's children.

    palera1n posix_spawns checkra1n and only waits for it. Killing palera1n
    alone leaves checkra1n running, and it exploits the next DFU device it
    sees, so give up on the whole process group instead.
    """
    # A plain SIGTERM (pkill, logout) would end Orchard without unwinding;
    # turn it into SystemExit while the group runs so it is cleaned up too.
    previous = None
    if threading.current_thread() is threading.main_thread():
        previous = signal.signal(signal.SIGTERM, _exit_on_sigterm)
    try:
        with subprocess.Popen(argv, start_new_session=True, **kwargs) as process:
            try:
                returncode = process.wait(timeout=timeout)
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                raise
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    if returncode:
        raise subprocess.CalledProcessError(returncode, argv)


def start_pongo(device, image, log, say, *, chip, board, product, seconds=30):
    """Start Pongo once on the selected device in DFU; never retried.

    Every boot path goes through here so they share the cable hint, the
    process-group cleanup and the failure message.
    """
    executable = shutil.which('palera1n')
    if not executable:
        raise ValueError('palera1n is required on the host to boot Pongo')
    usb.selected(device['location'], device['ecid'], chip=chip, board=board, mode='dfu')
    # Resolved while the device is present: its sysfs node goes away after checkm8.
    control = replug.port_control(device['location'])
    say('Booting Pongo')
    if control:
        say('Wait for the Apple logo to appear. Orchard resets the USB port if the device does not '
            'come back; if the logo stays for about 15 seconds, disconnect and reconnect the cable '
            'at the device once.')
    else:
        say('Wait for the Apple logo to appear. If it does not disappear after a few seconds, '
            'disconnect and reconnect the cable at the device once.')
    watcher = replug.PortReset(log.name, control) if control else None
    if watcher:
        watcher.start()
    try:
        run_process_group([executable, '-p', '-k', str(image)],
                          stdout=log, stderr=subprocess.STDOUT, timeout=120)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError('Pongo bootstrap failed or outcome unknown; inspect USB before any retry. '
                           f'Log: {log.name}') from error
    finally:
        if watcher:
            watcher.stop()
            watcher.join()
    if watcher and watcher.resets:
        say(f'USB port reset {watcher.resets}x to bring the device back after checkm8')
    return wait_mode(device, 'pongo', chip=chip, board=board, product=product, seconds=seconds)


def pongo_from_dfu(device, image, progress, *, profile, chip, board, product):
    """Start Pongo when the device is in DFU, logging palera1n under the device's state.

    A device already in Pongo is used as it is.
    """
    if device['mode'] != 'dfu':
        return
    logs = private_directory(DeviceState(profile, device['ecid']).root / 'logs')
    with tempfile.NamedTemporaryFile(prefix='palera1n-', suffix='.log', dir=logs, delete=False) as log:
        start_pongo(device, image, log, progress.phase, chip=chip, board=board, product=product)


def boot(device, assets, manifest, progress, *, profile, chip, board, product):
    if device['mode'] == 'linux':
        progress.phase('Using running recovery; no reboot or upload (timing starts at resume)')
        return
    pongo_from_dfu(device, assets['pongo'], progress, profile=profile, chip=chip, board=board, product=product)
    progress.phase('Loading recovery')
    usb.selected(device['location'], device['ecid'], chip=chip, board=board)
    progress.reset_transfer()
    pongo.upload(assets['recovery'], device['location'], manifest['assets']['recovery']['sha256'], progress.transfer)
    progress.phase('Waiting for recovery USB')
    wait_mode(device, 'linux', chip=chip, board=board, product=product, seconds=120)


def start_loader(device, loader, progress, *, profile, chip, board, product, seconds=30):
    """Boot the bare m1n1 loader ({'pongo', 'm1n1'} files) through Pongo; return its proxy serial port."""
    if device['mode'] not in ('dfu', 'pongo'):
        raise ValueError('Put the device in DFU mode first; a running system is not rebooted')
    pongo_from_dfu(device, loader['pongo'], progress, profile=profile, chip=chip, board=board, product=product)
    before = set(usb.m1n1_ports())
    progress.phase('Loading the bootloader')
    usb.selected(device['location'], device['ecid'], chip=chip, board=board)
    progress.reset_transfer()
    pongo.upload(loader['m1n1'], device['location'], digest(loader['m1n1']), progress.transfer)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ports = [port for port in usb.m1n1_ports() if port not in before]
        if len(ports) == 1:
            return ports[0]
        if len(ports) > 1:
            raise ValueError('More than one m1n1 device is connected')
        time.sleep(0.25)
    raise RuntimeError('The bootloader did not appear on USB; no retry was performed')

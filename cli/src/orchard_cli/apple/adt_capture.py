"""Fetch an opaque ADT through the recovery endpoint; never save it implicitly."""
import re

from orchard_cli.serial import SerialPort
from orchard_hardware.apple.adt import validate
SIZE_COMMAND = '/sbin/capture-adt --size'
MAX_ADT = 16 * 1024 * 1024


def capture(select_endpoint) -> bytes:
    device = select_endpoint()
    port, location = device['serial_port'], device['location']

    def check():
        current = select_endpoint()
        if (current['location'] != location or current['serial_port'] != port or
                (current.get('ecid'), current.get('boot_id')) != (device.get('ecid'), device.get('boot_id'))):
            raise ValueError('Recovery USB endpoint changed; capture stopped')

    with SerialPort(port, check) as serial:
        code, size_text = serial.run(SIZE_COMMAND, timeout=15)
        entries = size_text.strip().splitlines()
        if (code != 0 or len(entries) != 1 or
                not re.fullmatch(r'[0-9a-fA-F]{1,8}', entries[0])):
            raise ValueError('Invalid recovery ADT size response')
        size = int(entries[0], 16)
        if not 8 <= size <= MAX_ADT:
            raise ValueError('Recovery ADT length outside bounds')
        blob = serial.capture_exact('/sbin/capture-adt', size, timeout=90)
    check()
    if len(blob) != size:
        raise ValueError('Captured ADT length differs')
    if validate(blob) != device['ecid']:
        raise ValueError('Captured ADT identity differs from the USB device identity')
    return blob

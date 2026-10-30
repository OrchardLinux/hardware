"""Discover Apple USB devices from Linux sysfs without a USB Python dependency."""
from pathlib import Path
import re
import uuid

MODES = {0x1227: 'dfu', 0x4141: 'pongo', 0x4142: 'linux', 0x4144: 'ram'}


def devices(root=Path('/sys/bus/usb/devices')):
    found = []
    for node in root.iterdir():
        try:
            vid = int((node / 'idVendor').read_text().strip(), 16)
            pid = int((node / 'idProduct').read_text().strip(), 16)
            serial = (node / 'serial').read_text().strip() if (node / 'serial').exists() else ''
        except (OSError, ValueError):
            continue
        if vid != 0x05ac or not re.fullmatch(r'[0-9]+-[0-9]+(?:\.[0-9]+)*', node.name):
            continue
        boot = re.search(r'\bBOOT:([0-9a-fA-F-]{36})\b', serial)
        ecid = re.search(r'\bECID:([0-9a-fA-F]+)\b', serial)
        chip = re.search(r'\bCPID:([0-9a-fA-F]+)\b', serial)
        board = re.search(r'\bBDID:([0-9a-fA-F]+)\b', serial)
        ports = {str(Path('/dev') / p.name) for p in node.glob('**/tty/ttyACM*')}
        if len(ports) > 1:
            raise ValueError('Ambiguous USB serial ports on a device')
        product = (node / 'product').read_text().strip() if (node / 'product').exists() else ''
        found.append(dict(location=node.name, mode=MODES.get(pid, 'other'),
                          ecid=int(ecid.group(1), 16) if ecid else None,
                          boot_id=str(uuid.UUID(boot.group(1))) if boot else None,
                          chip=int(chip.group(1), 16) if chip else None,
                          board=int(board.group(1), 16) if board else None,
                          pwned='YOLO:checkra1n' in serial, product=product,
                          serial_port=next(iter(ports), None)))
    return found


def m1n1_ports(root=Path('/sys/bus/usb/devices')):
    """Proxy (first CDC-ACM interface) tty of every m1n1 USB device."""
    found = []
    for node in root.iterdir():
        try:
            if (int((node / 'idVendor').read_text().strip(), 16), int((node / 'idProduct').read_text().strip(), 16)) \
                    != (0x1209, 0x316d):
                continue
        except (OSError, ValueError):
            continue
        ports = sorted(str(Path('/dev') / p.name) for p in node.glob(node.name + ':*.0/tty/ttyACM*'))
        if ports:
            found.append(ports[0])
    return found

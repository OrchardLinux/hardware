#!/usr/bin/env python3
"""Upload a PongoOS RAM image over libusb."""

# PongoOS/pongoterm copyright (C) 2019-2021 checkra1n team.
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.
from __future__ import annotations

import ctypes as C
import hashlib
import os
from ctypes.util import find_library
from pathlib import Path
import re
import shutil
import struct
import stat
import subprocess
import sys
import time

VENDOR = 0x05AC
PRODUCT = 0x4141
MAX_IMAGE = 128 * 1024 * 1024
BULK_ENDPOINT = 0x02
CHUNK = 1024 * 1024
CONTROL_TIMEOUT_MS = 8000
BULK_TIMEOUT_MS = 30000


class PongoError(RuntimeError):
    pass


class UploadUncertain(PongoError):
    """Some USB data may already have reached Pongo; do not retry blindly."""


class DeviceDescriptor(C.Structure):
    _fields_ = [('bLength', C.c_uint8), ('bDescriptorType', C.c_uint8),
                ('bcdUSB', C.c_uint16), ('bDeviceClass', C.c_uint8),
                ('bDeviceSubClass', C.c_uint8), ('bDeviceProtocol', C.c_uint8),
                ('bMaxPacketSize0', C.c_uint8), ('idVendor', C.c_uint16),
                ('idProduct', C.c_uint16), ('bcdDevice', C.c_uint16),
                ('iManufacturer', C.c_uint8), ('iProduct', C.c_uint8),
                ('iSerialNumber', C.c_uint8), ('bNumConfigurations', C.c_uint8)]


def library_path():
    candidates = [find_library('usb-1.0')]
    pkg_config = shutil.which('pkg-config')
    if pkg_config:
        try:
            libdir = subprocess.run([pkg_config, '--variable=libdir', 'libusb-1.0'],
                                    check=True, capture_output=True, text=True,
                                    timeout=5).stdout.strip()
            candidates.append(str(Path(libdir) / ('libusb-1.0.dylib' if sys.platform == 'darwin'
                                                    else 'libusb-1.0.so.0')))
        except (OSError, subprocess.SubprocessError):
            pass
    if sys.platform == 'darwin':
        brew = shutil.which('brew')
        if brew:
            try:
                prefix = subprocess.run([brew, '--prefix', 'libusb'], check=True,
                                        capture_output=True, text=True, timeout=5).stdout.strip()
                candidates.append(str(Path(prefix) / 'lib/libusb-1.0.dylib'))
            except (OSError, subprocess.SubprocessError):
                pass
        candidates.append('libusb-1.0.dylib')
    else:
        candidates.append('libusb-1.0.so.0')
    for candidate in filter(None, candidates):
        try:
            return C.CDLL(candidate)
        except OSError:
            continue
    raise PongoError('libusb-1.0 is unavailable; install libusb for this host')


class LibUSB:
    def __init__(self):
        self.lib = library_path()
        self._bind('libusb_init', C.c_int, C.POINTER(C.c_void_p))
        self._bind('libusb_exit', None, C.c_void_p)
        self._bind('libusb_get_device_list', C.c_ssize_t, C.c_void_p,
                   C.POINTER(C.POINTER(C.c_void_p)))
        self._bind('libusb_free_device_list', None, C.POINTER(C.c_void_p), C.c_int)
        self._bind('libusb_get_device_descriptor', C.c_int, C.c_void_p,
                   C.POINTER(DeviceDescriptor))
        self._bind('libusb_get_bus_number', C.c_uint8, C.c_void_p)
        self._bind('libusb_get_port_numbers', C.c_int, C.c_void_p,
                   C.POINTER(C.c_uint8), C.c_int)
        self._bind('libusb_open', C.c_int, C.c_void_p, C.POINTER(C.c_void_p))
        self._bind('libusb_close', None, C.c_void_p)
        self._bind('libusb_kernel_driver_active', C.c_int, C.c_void_p, C.c_int)
        self._bind('libusb_detach_kernel_driver', C.c_int, C.c_void_p, C.c_int)
        self._bind('libusb_set_configuration', C.c_int, C.c_void_p, C.c_int)
        self._bind('libusb_claim_interface', C.c_int, C.c_void_p, C.c_int)
        self._bind('libusb_release_interface', C.c_int, C.c_void_p, C.c_int)
        self._bind('libusb_control_transfer', C.c_int, C.c_void_p, C.c_uint8,
                   C.c_uint8, C.c_uint16, C.c_uint16, C.POINTER(C.c_uint8),
                   C.c_uint16, C.c_uint)
        self._bind('libusb_bulk_transfer', C.c_int, C.c_void_p, C.c_uint8,
                   C.POINTER(C.c_uint8), C.c_int, C.POINTER(C.c_int), C.c_uint)
        self._bind('libusb_error_name', C.c_char_p, C.c_int)
        self.context = C.c_void_p()
        self.handle = C.c_void_p()
        self.claimed = False

    def _bind(self, name, result, *args):
        fn = getattr(self.lib, name)
        fn.argtypes = args
        fn.restype = result

    def error(self, code):
        label = self.lib.libusb_error_name(code)
        return label.decode(errors='replace') if label else f'libusb error {code}'

    def open(self, target):
        result = self.lib.libusb_init(C.byref(self.context))
        if result < 0:
            raise PongoError('libusb_init: ' + self.error(result))
        devices = C.POINTER(C.c_void_p)()
        count = self.lib.libusb_get_device_list(self.context, C.byref(devices))
        if count < 0:
            raise PongoError('USB discovery: ' + self.error(count))
        matches = []
        try:
            for index in range(count):
                device = devices[index]
                desc = DeviceDescriptor()
                if self.lib.libusb_get_device_descriptor(device, C.byref(desc)) != 0:
                    continue
                if (desc.idVendor, desc.idProduct) != (VENDOR, PRODUCT):
                    continue
                ports = (C.c_uint8 * 8)()
                length = self.lib.libusb_get_port_numbers(device, ports, len(ports))
                if target is None:
                    matches.append(device)
                elif length > 0:
                    bus = self.lib.libusb_get_bus_number(device)
                    if matches_target(target, bus, tuple(ports[:length])):
                        matches.append(device)
            if len(matches) != 1:
                raise PongoError(f'Expected exactly one Pongo device; found {len(matches)}')
            result = self.lib.libusb_open(matches[0], C.byref(self.handle))
            if result < 0:
                raise PongoError('Opening selected Pongo: ' + self.error(result))
        finally:
            self.lib.libusb_free_device_list(devices, 1)
        active = self.lib.libusb_kernel_driver_active(self.handle, 0)
        if active == 1:
            result = self.lib.libusb_detach_kernel_driver(self.handle, 0)
            if result < 0:
                raise PongoError('Detaching USB driver: ' + self.error(result))
        elif active < 0 and active != -12:  # NOT_SUPPORTED on macOS
            raise PongoError('USB driver state: ' + self.error(active))
        result = self.lib.libusb_set_configuration(self.handle, 1)
        if result < 0:
            raise PongoError('USB configuration: ' + self.error(result))
        result = self.lib.libusb_claim_interface(self.handle, 0)
        if result < 0:
            raise PongoError('USB interface: ' + self.error(result))
        self.claimed = True

    def close(self):
        if self.handle.value:
            if self.claimed:
                self.lib.libusb_release_interface(self.handle, 0)
            self.lib.libusb_close(self.handle)
            self.handle = C.c_void_p()
        if self.context.value:
            self.lib.libusb_exit(self.context)
            self.context = C.c_void_p()

    def control(self, direction, request, value=0, data=b'', length=0):
        if direction == 0x21:
            payload = (C.c_uint8 * len(data)).from_buffer_copy(data)
            expected = len(data)
        else:
            payload = (C.c_uint8 * length)()
            expected = length
        result = self.lib.libusb_control_transfer(
            self.handle, direction, request, value, 0, payload, expected,
            CONTROL_TIMEOUT_MS)
        if result < 0:
            raise PongoError(f'USB control {request}: {self.error(result)}')
        if direction == 0x21 and result != expected:
            raise PongoError(f'USB control {request}: short write {result}/{expected}')
        return bytes(payload[:result])

    def bulk(self, block):
        payload = (C.c_uint8 * len(block)).from_buffer_copy(block)
        done = 0
        while done < len(block):
            transferred = C.c_int()
            remaining = len(block) - done
            result = self.lib.libusb_bulk_transfer(
                self.handle, BULK_ENDPOINT, C.cast(C.byref(payload, done),
                                                    C.POINTER(C.c_uint8)),
                remaining, C.byref(transferred), BULK_TIMEOUT_MS)
            if result < 0:
                raise UploadUncertain('USB bulk transfer: ' + self.error(result))
            if not 0 < transferred.value <= remaining:
                raise UploadUncertain('USB bulk transfer made no progress')
            done += transferred.value


def matches_target(target, bus, ports):
    if sys.platform == 'linux':
        match = re.fullmatch(r'([0-9]+)-([0-9]+(?:\.[0-9]+)*)', str(target))
        if not match:
            raise PongoError('Invalid selected Linux USB port')
        return bus == int(match.group(1)) and ports == tuple(
            int(p) for p in match.group(2).split('.'))
    if sys.platform == 'darwin':
        location = int(target)
        # IOKit locationID uses one nibble per downstream port after the bus byte.
        chain = []
        for shift in range(20, -1, -4):
            port = (location >> shift) & 0xf
            if port:
                chain.append(port)
            elif chain:
                break
        if not chain:
            raise PongoError('Invalid selected macOS USB location')
        return ports == tuple(chain)
    raise PongoError('Unsupported USB host')


def wait_prompt(usb, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = usb.control(0xa1, 2, length=1)
        if len(state) != 1:
            raise PongoError('Invalid Pongo shell state')
        output = usb.control(0xa1, 1, length=4096)
        if len(output) > 4096:
            raise PongoError('Invalid Pongo shell output')
        if state == b'\x00':
            return
        time.sleep(0.05)
    raise PongoError('Pongo shell prompt timed out')


def upload(image: Path, target=None, expected_sha256: str | None = None, progress=None):
    """Send one RAM image to the sole Pongo (or selected port) and issue bootm."""
    if image.is_symlink():
        raise PongoError('Expected a regular RAM image')
    with image.open('rb') as source:
        size = source.seek(0, 2)
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode) or not 0 < size <= MAX_IMAGE:
            raise PongoError('RAM image is not regular or exceeds Pongo upload limit')
        source.seek(0)
        digest = hashlib.sha256()
        for block in iter(lambda: source.read(CHUNK), b''):
            digest.update(block)
        actual_sha256 = digest.hexdigest()
        if expected_sha256 is not None and actual_sha256 != expected_sha256:
            raise PongoError('RAM image differs from its expected SHA-256')
        source.seek(0)
        usb = LibUSB()
        try:
            usb.open(target)
            wait_prompt(usb)
            usb.control(0x21, 4, value=0xffff)
            begun = False
            try:
                begun = True
                usb.control(0x21, 1, data=struct.pack('<I', size))
                sent = hashlib.sha256()
                count = 0
                for block in iter(lambda: source.read(CHUNK), b''):
                    usb.bulk(block)
                    sent.update(block)
                    count += len(block)
                    if progress:
                        progress(count, size)
                if sent.hexdigest() != actual_sha256:
                    raise UploadUncertain('RAM image changed during transfer; inspect USB before retry')
                wait_prompt(usb)
                usb.control(0x21, 4, value=0xffff)
                usb.control(0x21, 4, value=1)
                usb.control(0x21, 3, data=b'bootm\n')
            except PongoError as exc:
                if begun:
                    raise UploadUncertain(str(exc) + '; inspect USB before any retry') from exc
                raise
        finally:
            usb.close()
        return {'bytes': size, 'sha256': actual_sha256}

"""Linux: reset the device's USB port when it does not come back after checkm8.

After "Checkmate!" the device drops off USB and must re-enumerate in download
mode. Some Linux hosts never see it return until the cable is replugged at the
device. Disabling and re-enabling the hub port forces that re-detection. The
port's disable control is root-only; setup-host's udev rule makes it writable
for the USB access group when a device enters DFU.
"""
import os
from pathlib import Path
import threading

SYSFS = Path('/sys/bus/usb/devices')
DISCONNECTED = 'DFU mode device disconnected'
RETURNED = ('Entered download mode', 'Download mode device found')


def port_control(location, root=SYSFS):
    """The writable disable control of the port a device is on, or None."""
    # Only Linux names a device by its sysfs path; macOS gives a number.
    if not isinstance(location, str):
        return None
    try:
        control = (root / location / 'port').resolve(strict=True) / 'disable'
    except (OSError, RuntimeError):
        return None
    return control if control.is_file() and os.access(control, os.W_OK) else None


class PortReset(threading.Thread):
    """Watch palera1n's log; reset the port if the device does not return."""

    def __init__(self, log, control, *, settle=2.5, wait=5.0, attempts=3):
        super().__init__(daemon=True)
        self.log, self.control = Path(log), Path(control)
        self.settle, self.wait, self.attempts = settle, wait, attempts
        self.resets = 0
        self._stopping = threading.Event()

    def stop(self):
        self._stopping.set()

    def _after_checkmate(self):
        try:
            text = self.log.read_text(errors='replace')
        except OSError:
            return None
        index = text.find('Checkmate!')
        return None if index < 0 else text[index:]

    def _returned(self):
        text = self._after_checkmate()
        return bool(text) and any(line in text for line in RETURNED)

    def _await(self, seconds, condition):
        for _ in range(max(1, int(seconds * 10))):
            if condition():
                return True
            if self._stopping.wait(0.1):
                return False
        return condition()

    def run(self):
        dropped = lambda: DISCONNECTED in (self._after_checkmate() or '')
        while not self._stopping.is_set() and not dropped():
            self._stopping.wait(0.1)
        if self._stopping.is_set() or self._await(self.settle, self._returned):
            return
        for _ in range(self.attempts):
            if self._stopping.is_set():
                return
            try:
                self.control.write_text('1')
            except OSError:
                return
            try:
                self._stopping.wait(0.5)
            finally:
                try:
                    self.control.write_text('0')
                    restored = True
                except OSError:
                    restored = False
            if not restored:
                return
            self.resets += 1
            if self._await(self.wait, self._returned):
                return

#!/usr/bin/python3
"""VM-only hardware bridge: report backlight state and accept shutdown only."""
import errno
import os
import select
import subprocess
import threading
import time

BASE = '/dev/virtio-ports/'
BRIGHTNESS = '/sys/class/backlight/20e200080.backlight/brightness'
MAXIMUM = '/sys/class/backlight/20e200080.backlight/max_brightness'

def open_port(name):
    while True:
        try:
            return os.open(BASE + name, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError:
            time.sleep(0.5)

def report_brightness():
    fd = open_port('org.orchard.display')
    while True:
        try:
            with open(BRIGHTNESS) as f:
                value = int(f.read())
            with open(MAXIMUM) as f:
                maximum = int(f.read())
            os.write(fd, f'{value} {maximum}\n'.encode('ascii'))
        except (OSError, ValueError):
            pass
        time.sleep(0.25)

def commands():
    fd = open_port('org.orchard.control')
    pending = b''
    while True:
        ready, _, _ = select.select([fd], [], [], 1)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 256)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EINTR):
                continue
            time.sleep(0.2)
            continue
        if not chunk:
            pending = b''
            time.sleep(0.2)
            continue
        pending += chunk
        if len(pending) > 256:
            pending = b''
        while b'\n' in pending:
            line, pending = pending.split(b'\n', 1)
            if line == b'shutdown':
                subprocess.run(['/usr/bin/systemctl', 'poweroff'], check=False)


threading.Thread(target=report_brightness, daemon=True).start()
commands()

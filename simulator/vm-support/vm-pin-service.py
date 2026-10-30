#!/usr/bin/python3
"""Disposable VM PIN authority; not suitable for product deployment."""
import hashlib
import hmac
import os
from pathlib import Path
import socket
import stat
import struct
import time

DIRECTORY = '/run/orchard-vm-pin'
SOCKET = DIRECTORY + '/auth.sock'
CREDENTIAL = Path('/etc/orchard-vm/pin')
os.makedirs(DIRECTORY, mode=0o750, exist_ok=True)
os.chown(DIRECTORY, 0, 1000)
os.chmod(DIRECTORY, 0o750)
try:
    os.unlink(SOCKET)
except FileNotFoundError:
    pass
server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
server.bind(SOCKET)
os.chown(SOCKET, 0, 1000)
os.chmod(SOCKET, 0o660)
server.listen(8)
failures = 0
locked_until = 0.0
while True:
    conn, _ = server.accept()
    with conn:
        conn.settimeout(2)
        try:
            _, uid, _ = struct.unpack('3i', conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
            if uid not in (0, 1000):
                continue
            pin = b''
            while b'\n' not in pin and len(pin) < 16:
                chunk = conn.recv(16 - len(pin))
                if not chunk: break
                pin += chunk
            pin = pin.rstrip(b'\r\n')
            now = time.monotonic()
            if locked_until and now >= locked_until:
                failures, locked_until = 0, 0.0
            good = False
            if now >= locked_until and len(pin) == 6 and pin.isdigit():
                st = CREDENTIAL.stat()
                if not stat.S_ISREG(st.st_mode) or st.st_uid != 0 or st.st_mode & 0o077:
                    raise ValueError('unsafe credential')
                salt, expected = (bytes.fromhex(x) for x in CREDENTIAL.read_text().strip().split(':'))
                good = hmac.compare_digest(hashlib.pbkdf2_hmac('sha256', pin, salt, 200000), expected)
            if good:
                failures = 0
            elif now >= locked_until:
                failures += 1
                if failures >= 5: locked_until = time.monotonic() + 60
            conn.sendall(b'OK\n' if good else b'NO\n')
        except (OSError, ValueError):
            pass

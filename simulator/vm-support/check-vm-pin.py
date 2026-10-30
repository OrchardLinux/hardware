#!/usr/bin/python3
"""PAM client for the disposable simulator PIN authority; not for production."""
import os
import socket
import sys

try:
    if os.environ.get('PAM_USER') != 'tablet':
        sys.exit(1)
    pin = sys.stdin.buffer.readline(32).rstrip(b'\r\n')
    if len(pin) != 6 or not pin.isdigit():
        sys.exit(1)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(3)
        conn.connect('/run/orchard-vm-pin/auth.sock')
        conn.sendall(pin + b'\n')
        sys.exit(0 if conn.recv(8) == b'OK\n' else 1)
except OSError:
    sys.exit(1)

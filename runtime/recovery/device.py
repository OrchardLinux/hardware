#!/usr/bin/python3
"""The recovery's device operations, as /usr/bin/orchard-device OPERATION [BASE64-JSON].

Three operations: `bootstrap` (over USB serial: authorize the host's SSH key,
start SSH, return the pinned host key), `status`, and `start` (ask PID 1 to
switch into an installed root, see start.py). Installing runs the host's
install_device.py over that SSH session; this file knows nothing about
storage. Output is JSON lines: progress events, then one result or
error.
"""
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import identity

PROTOCOL = 1
RELEASE = Path('/etc/orchard/release.json')
KEY = r'ssh-ed25519 [A-Za-z0-9+/=]{60,128}'


def emit(event, **values):
    print(json.dumps({'protocol': PROTOCOL, 'event': event, **values}), flush=True)


def device():
    record = identity.cached()
    if record is None:
        raise ValueError('Device identity is not published yet')
    return record


def status(_):
    record = device()
    return {'release': json.loads(RELEASE.read_text())['release'], **record}


def bootstrap(request):
    """Serial API: own RAM key/service state; return only connection data."""
    record = device()
    if type(request.get('ecid')) is not int or any(request.get(key) != record[key]
                                                   for key in ('profile', 'ecid', 'boot_id')):
        raise ValueError('SSH bootstrap identity differs from this recovery boot')
    public = request.get('public_key', '')
    if not isinstance(public, str) or not re.fullmatch(KEY, public):
        raise ValueError('Expected one Ed25519 public key')
    subprocess.run(['/sbin/authorize-key'], input=(public + '\n').encode(), check=True, capture_output=True)
    directory = Path('/run/recovery')
    directory.mkdir(mode=0o700, exist_ok=True)
    pidfile = directory / 'dropbear.pid'
    running = False
    try:
        pid = int(pidfile.read_text())
        running = pid > 1 and (Path('/proc') / str(pid) / 'cmdline').read_bytes().split(b'\0')[0] == b'/usr/sbin/dropbear'
    except (OSError, ValueError):
        pass
    process = None
    if not running:
        pidfile.unlink(missing_ok=True)
        fd = os.open(directory / 'ssh.log', os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'ab') as log:
            process = subprocess.Popen(['/sbin/recovery-start-ssh'], stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 10
    while True:
        if process is not None and process.poll() is not None:
            raise RuntimeError('Recovery SSH daemon failed: ' + (directory / 'ssh.log').read_text()[-2000:])
        try:
            key = (directory / 'ssh_host_ed25519_key.pub').read_text().strip()
            if pidfile.is_file() and re.fullmatch(KEY, key):
                return {**record, 'host': '172.16.42.1', 'port': 22, 'host_key': key}
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise RuntimeError('Recovery SSH daemon readiness timed out')
        time.sleep(0.05)


def start(request):
    """Ask PID 1 to start an installed root in this boot; it checks and switches within a second."""
    target = {key: str(uuid.UUID(request[key])) for key in ('root', 'boot')}
    path = Path('/run/orchard/start.json')
    if path.exists():
        raise ValueError('A start was already requested in this boot')
    temporary = path.with_suffix('.part')
    temporary.write_text(json.dumps(target))
    temporary.replace(path)
    return {'starting': target['root']}


OPERATIONS = {'status': status, 'bootstrap': bootstrap, 'start': start}


def main():
    try:
        operation = sys.argv[1]
        request = json.loads(base64.urlsafe_b64decode(sys.argv[2])) if len(sys.argv) == 3 else {}
        if request.pop('protocol', PROTOCOL) != PROTOCOL:
            raise ValueError('Incompatible device protocol')
        if operation not in OPERATIONS:
            raise ValueError('Unknown device operation')
        emit('result', operation=operation, result=OPERATIONS[operation](request))
    except Exception as error:
        emit('error', message=str(error), retry=False)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

"""Shell, commands and file transfer for a running RAM payload.

`orchard build` adds the usb-session helper and this host's RAM key to every
payload unless payload.toml says transport = false. The device's host key is
generated per boot and pinned over USB serial, which is physical and trusted.
SSH carries everything else. Without SSH, push and pull fall back to
length-prefixed serial transfers checked by SHA-256.
"""
import hashlib
from importlib import resources
from pathlib import Path
import re
import shlex
import subprocess
import time

from .serial import SerialError, SerialPort
from .state import cache_root, private_directory
from .transports.ssh import host_key, ssh_argv, wait_authenticated

HOST = '172.16.42.1'
HELPER = 'usr/libexec/orchard/usb-session'
AUTHORIZED = 'etc/orchard/authorized_keys'
SERIAL_LIMIT = 16 * 1024 * 1024
PATH = 'export PATH=/bin:/sbin:/usr/bin:/usr/sbin; '


def _directory():
    return private_directory(cache_root() / 'private/ram-ssh')


def identity():
    """This host's key for RAM payloads; created once, never leaves the host."""
    key = _directory() / 'id_ed25519'
    if key.is_symlink():
        raise ValueError('Linked RAM SSH key refused')
    if not key.exists():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'orchard-ram',
                        '-f', str(key)], check=True, capture_output=True, timeout=20)
    key.chmod(0o600)
    return key


def payload_files():
    """RAM-root files that enable the USB session in a payload."""
    helper = resources.files('orchard') / 'usb-session'
    return {HELPER: Path(str(helper)), AUTHORIZED: identity().with_suffix('.pub')}


def target():
    """The one running RAM payload on USB."""
    from .apple import usb
    found = [item for item in usb.devices() if item['mode'] == 'ram']
    if len(found) != 1:
        raise ValueError('Connect exactly one running RAM payload (none or several found)')
    device = found[0]
    if not device.get('serial_port'):
        raise ValueError('The RAM payload has no USB serial port')
    return device


def _check(device):
    def check():
        from .apple import usb
        if not any(item['location'] == device['location'] and item.get('boot_id') == device.get('boot_id')
                   for item in usb.devices()):
            raise ValueError('The RAM payload disconnected or rebooted')
    return check


def connect(device=None):
    """SSH argv for the running payload, pinning its host key over serial once per boot."""
    device = device or target()
    boot = device.get('boot_id')
    if not boot:
        raise ValueError('The RAM payload has no boot identity; rebuild it with a current orchard')
    check = _check(device)
    hosts = _directory() / f'known_hosts-{boot}'
    if not hosts.is_file():
        with SerialPort(device['serial_port'], check) as serial:
            code, text = serial.run('cat /run/orchard/ssh_host_ed25519_key.pub', timeout=15)
        if code:
            raise ValueError('No SSH in this payload; its /init must run '
                             '/' + HELPER + ' (or build without transport = false)')
        for old in _directory().glob('known_hosts-*'):
            old.unlink()
        hosts.write_text(HOST + ' ' + host_key(text) + '\n')
        hosts.chmod(0o600)
    argv = ssh_argv(identity(), hosts, HOST, 22)
    wait_authenticated(argv, check, seconds=20)
    return argv


def shell():
    argv = connect()
    return subprocess.run([*argv[:-1], '-t', argv[-1]]).returncode


def run(command):
    argv = connect()
    return subprocess.run([*argv, PATH + ' '.join(map(shlex.quote, command))]).returncode


def _sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def _remote_sha256(text):
    match = re.match(r'([0-9a-f]{64})\s', text.strip() + ' ')
    if not match:
        raise ValueError('Unexpected checksum reply: ' + text.strip()[-200:])
    return match.group(1)


def push(local, remote, *, serial=False):
    """Copy a host file to the payload, written atomically and checked by SHA-256."""
    local, expected = Path(local), _sha256(local)
    part = remote + '.part'
    quoted, quoted_part = shlex.quote(remote), shlex.quote(part)
    if serial:
        device = target()
        size = local.stat().st_size
        if size > SERIAL_LIMIT:
            raise ValueError('Serial transfers are limited to 16 MiB; use SSH')
        with SerialPort(device['serial_port'], _check(device)) as port:
            port.send_exact(f'head -c {size} > {quoted_part}', local.read_bytes(), timeout=60 + size // 8000)
            code, text = port.run(f'sha256sum {quoted_part}')
            actual = _remote_sha256(text) if code == 0 else None
            if actual == expected:
                code, text = port.run(f'mv {quoted_part} {quoted}')
    else:
        argv = connect()
        with local.open('rb') as stream:
            subprocess.run([*argv, f'cat > {quoted_part}'], stdin=stream, check=True)
        reply = subprocess.run([*argv, f'sha256sum {quoted_part}'], capture_output=True, text=True, check=True)
        actual = _remote_sha256(reply.stdout)
        if actual == expected:
            subprocess.run([*argv, f'mv {quoted_part} {quoted}'], check=True)
    if actual != expected:
        raise ValueError(f'{remote}: checksum mismatch after transfer; left as {part}')
    print(f'{local} -> {remote} ({local.stat().st_size} bytes, sha256 {expected[:16]})')


def pull(remote, local, *, serial=False):
    """Copy a payload file to the host, checked by SHA-256."""
    local, quoted = Path(local), shlex.quote(remote)
    partial = local.with_name(local.name + '.part')
    if serial:
        device = target()
        with SerialPort(device['serial_port'], _check(device)) as port:
            code, text = port.run(f'wc -c < {quoted}; sha256sum {quoted}')
            lines = text.split()
            if code or len(lines) < 2 or not lines[0].isdigit():
                raise ValueError(f'{remote}: not readable on the device')
            size, expected = int(lines[0]), _remote_sha256(' '.join(lines[1:]))
            partial.write_bytes(port.capture_exact(f'cat {quoted}', size, timeout=60 + size // 8000))
    else:
        argv = connect()
        reply = subprocess.run([*argv, f'sha256sum {quoted}'], capture_output=True, text=True, check=True)
        expected = _remote_sha256(reply.stdout)
        with partial.open('wb') as stream:
            subprocess.run([*argv, f'cat {quoted}'], stdout=stream, check=True)
    if _sha256(partial) != expected:
        raise ValueError(f'{remote}: checksum mismatch after transfer; left as {partial}')
    partial.replace(local)
    print(f'{remote} -> {local} ({local.stat().st_size} bytes, sha256 {expected[:16]})')

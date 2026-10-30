"""Serial-pinned SSH transport to the recovery; the key lives only for this session."""
import base64
from contextlib import contextmanager
import ipaddress
import json
from pathlib import Path
import re
import subprocess
import tempfile
import time

from orchard_cli.serial import SerialPort
from orchard_cli.contracts import Connection
from orchard_cli.state import BootState, DeviceState, private_directory
from orchard_cli.protocol import PROTOCOL, Client
from orchard_cli.progress import Progress
KEY = re.compile(r'^ssh-ed25519 [A-Za-z0-9+/=]+$')


def host_key(text):
    matches = [line.strip() for line in text.splitlines() if KEY.fullmatch(line.strip())]
    if len(matches) != 1:
        raise ValueError('Expected exactly one SSH host key over USB serial')
    return matches[0]


def bootstrap_reply(code, text, scope):
    records = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not records or any(not isinstance(row, dict) or row.get('protocol') != PROTOCOL for row in records):
        raise ValueError('Incompatible recovery SSH bootstrap protocol')
    errors = [row['message'] for row in records if row.get('event') == 'error']
    if code or errors:
        raise ValueError('Recovery SSH bootstrap failed: ' + '; '.join(errors) +
                         '\nLoad the matching current recovery from DFU, then run: orchard install')
    results = [row['result'] for row in records
               if row.get('event') == 'result' and row.get('operation') == 'bootstrap']
    if len(results) != 1:
        raise ValueError('Expected one SSH bootstrap result')
    result = results[0]
    if not isinstance(result, dict) or not isinstance(result.get('host_key'), str):
        raise ValueError('Invalid recovery SSH bootstrap result')
    if not scope.matches(result) or result.get('profile') != scope.device.profile:
        raise ValueError('Serial SSH bootstrap identity differs from the selected device/boot')
    host = str(ipaddress.IPv4Address(result['host']))
    port = result['port']
    if type(port) is not int or not 0 < port < 65536:
        raise ValueError('Invalid SSH endpoint port')
    return host, port, host_key(result['host_key'])


def ssh_argv(identity, hosts, host, port, timeout=8):
    return ['ssh', '-p', str(port), '-i', str(identity), '-o', 'IdentitiesOnly=yes',
            '-o', 'Compression=no', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=3',
            '-o', f'UserKnownHostsFile="{hosts}"', '-o', 'StrictHostKeyChecking=yes',
            '-o', 'BatchMode=yes', '-o', 'ControlMaster=no', '-o', 'ControlPath=none',
            '-o', 'ControlPersist=no', '-o', 'ConnectTimeout=' + str(timeout),
            '-o', 'HostKeyAlgorithms=ssh-ed25519', f'root@{host}']


def wait_authenticated(argv, check, seconds=30):
    """Wait for transport readiness, never resend a device phase or bootstrap."""
    deadline = time.monotonic() + seconds
    last_error = 'No SSH response'
    while True:
        check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('Recovery USB is visible, but authenticated USB SSH did not become ready. '
                               'No device phase was started. Last error: ' + last_error)
        try:
            result = subprocess.run([*argv, 'true'], stdin=subprocess.DEVNULL,
                                    capture_output=True, timeout=min(3, remaining))
            if result.returncode == 0:
                check()
                return
            last_error = result.stderr.decode(errors='replace').strip()[-2000:]
            if any(text in last_error.lower() for text in (
                    'host key verification failed', 'remote host identification has changed', 'permission denied')):
                raise ValueError('Pinned USB SSH authentication rejected: ' + last_error)
        except subprocess.TimeoutExpired:
            last_error = 'SSH connection/authentication timed out'
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(0.25, remaining))


def endpoint_check(scope, endpoint, select_endpoint):
    address = (endpoint['location'], endpoint['serial_port'])

    def check():
        current = select_endpoint()
        if (not scope.matches(current) or
                (current['location'], current['serial_port']) != address):
            raise ValueError('Selected device/boot or USB endpoint changed')
    return check


def verify_peer(connection):
    status = Client(connection, Progress()).call('status', timeout=8)
    if not connection.scope.matches(status) or status.get('profile') != connection.scope.device.profile:
        raise ValueError('Pinned SSH peer differs from the selected device/boot')
    connection.check()


@contextmanager
def session(device: DeviceState, select_endpoint, *, expected_boot=None):
    endpoint = select_endpoint()
    scope = BootState(device, endpoint['boot_id'])
    if not scope.matches(endpoint) or (expected_boot is not None and scope.boot_id != expected_boot):
        raise ValueError('Recovery identity differs from the selected device/boot')
    check = endpoint_check(scope, endpoint, select_endpoint)
    port = endpoint['serial_port']
    work = private_directory(scope.root / 'sessions')
    with tempfile.TemporaryDirectory(prefix='ssh-', dir=work) as directory:
        folder = Path(directory)
        identity = folder / 'id_ed25519'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(identity)],
                       check=True, capture_output=True, timeout=20)
        public = identity.with_suffix('.pub').read_text().split()
        if len(public) < 2 or not KEY.fullmatch(' '.join(public[:2])):
            raise ValueError('Generated SSH public key is invalid')
        identity.chmod(0o600)
        payload = base64.urlsafe_b64encode(json.dumps({
            'protocol': PROTOCOL, 'profile': device.profile, 'ecid': device.ecid,
            'boot_id': scope.boot_id, 'public_key': ' '.join(public[:2])}).encode()).decode()
        with SerialPort(port, check) as serial:
            code, text = serial.run('/usr/bin/orchard-device bootstrap ' + payload, timeout=20)
            host, ssh_port, pinned_key = bootstrap_reply(code, text, scope)
            check()
        hosts = folder / 'known_hosts'
        name = host if ssh_port == 22 else f'[{host}]:{ssh_port}'
        hosts.write_text(name + ' ' + pinned_key + '\n')
        hosts.chmod(0o600)
        argv = ssh_argv(identity, hosts, host, ssh_port)
        wait_authenticated(argv, check)
        connection = Connection(argv, scope, check)
        verify_peer(connection)
        yield connection


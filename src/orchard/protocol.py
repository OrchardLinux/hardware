"""Opaque phase RPC over an authenticated transport; no device shell recipes."""
import base64
import json
import subprocess

from .state import EndpointUnavailable

PROTOCOL = 1


class DeviceError(RuntimeError):
    def __init__(self, message, *, reported=False):
        super().__init__(message)
        self.reported = reported


class Client:
    """The recovery's one SSH operation: `status` ({release, profile, ecid, boot_id}).

    The SSH key itself is provisioned over USB serial (`bootstrap`, see
    transports/ssh.py); installing runs install_device.py (install_remote.py).
    """
    def __init__(self, connection, progress):
        self.connection = connection
        self.ssh, self.progress = connection.argv, progress

    def call(self, operation, request=None, *, timeout=120):
        if operation not in ('status', 'start'):
            raise ValueError('Unsupported phase operation')
        try:
            self.connection.check()
        except EndpointUnavailable as error:
            raise DeviceError(str(error)) from error
        payload = base64.urlsafe_b64encode(json.dumps({'protocol': PROTOCOL, **(request or {})}).encode()).decode()
        argv = self.ssh + ['/usr/bin/orchard-device ' + operation + ' ' + payload]
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise DeviceError(f'{operation} failed. Timed out') from error
        records, errors = [], []
        for line in result.stdout.splitlines():
            try:
                record = json.loads(line)
                if record.get('protocol') != PROTOCOL:
                    raise ValueError('Incompatible protocol response')
                if record['event'] == 'result':
                    records.append(record)
                elif record['event'] == 'error':
                    errors.append(record['message'])
                else:
                    self.progress.event(record)
            except (ValueError, KeyError) as error:
                errors.append('Invalid device response: ' + str(error))
        if result.returncode or errors or len(records) != 1 or records[0].get('operation') != operation:
            detail = '; '.join(errors) or result.stderr.decode(errors='replace')[-2000:]
            raise DeviceError(f'{operation} failed. {detail}', reported=bool(errors))
        return records[0]['result']

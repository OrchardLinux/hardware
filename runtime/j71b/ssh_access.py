"""Boot-scoped SSH retirement, shared by installed images; no hardware actions."""
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import time

RUN = Path('/run/orchard')
SESSION = Path('/run/recovery')
AUTHORIZED = Path('/root/.ssh/authorized_keys')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
PROC = Path('/proc')


def receipt(release):
    return {'boot_id': BOOT_ID.read_text().strip(), 'release': release}


def revoked(release):
    try:
        path = RUN / 'ssh-revoked.json'
        info = path.lstat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077 and
                json.loads(path.read_text()) == receipt(release))
    except (OSError, ValueError):
        return False


def write_marker(name, release):
    path = RUN / name
    temporary = path.with_suffix('.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(receipt(release), stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def server_pids(executable):
    result = set()
    for path in PROC.glob('[0-9]*'):
        try:
            if str((path / 'exe').readlink()) == executable:
                result.add(int(path.name))
        except (OSError, ValueError):
            pass
    return result


def connection_pid(executable):
    """Only the current RPC connection may live long enough to deliver its receipt."""
    pid = os.getpid()
    while pid > 1:
        process = PROC / str(pid)
        if str((process / 'exe').readlink()) == executable:
            return pid
        fields = (process / 'status').read_text().splitlines()
        pid = int(next(row.split()[1] for row in fields if row.startswith('PPid:')))
    raise ValueError('SSH retirement must run through its authenticated SSH connection')


def revoke(release, executable, stop_diagnostics):
    if revoked(release):
        raise ValueError('SSH access was already revoked for this boot')
    executable = str(Path(executable).resolve(strict=True))
    current = connection_pid(executable)
    source = SESSION / 'authorized_keys'
    # Both images must expose the RAM inode, never a persisted copy. Refuse
    # unexpected paths before changing authorization or stopping anything.
    if source.is_symlink() or AUTHORIZED.is_symlink() or not source.samefile(AUTHORIZED):
        raise ValueError('Expected RAM-backed authorized_keys bind mount')
    mounts = Path('/proc/self/mountinfo').read_text().splitlines()
    if not any(row.split()[4] == str(AUTHORIZED) for row in mounts):
        raise ValueError('authorized_keys is not a bind mount')
    write_marker('ssh-revoking.json', release)
    # Truncate the inode FIRST, so existing bind mounts cannot authorize new
    # connections even if later teardown fails. Markers block all restart paths.
    fd = os.open(source, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    os.close(fd)
    stop_diagnostics()  # Image-owned init integration; stops only the listener.
    for pid in server_pids(executable) - {current}:
        try:
            handle = os.pidfd_open(pid)
            try:
                if str((PROC / str(pid) / 'exe').readlink()) == executable:
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
            finally:
                os.close(handle)
        except ProcessLookupError:
            pass
        except FileNotFoundError:
            pass
    deadline = time.monotonic() + 5
    while server_pids(executable) - {current}:
        if time.monotonic() >= deadline:
            raise RuntimeError('Some SSH connections did not retire')
        time.sleep(0.02)
    subprocess.run(['umount', str(AUTHORIZED)], check=True, capture_output=True, timeout=5)
    AUTHORIZED.unlink()
    for name in ('authorized_keys', 'ssh_host_ed25519_key', 'ssh_host_ed25519_key.pub'):
        (SESSION / name).unlink(missing_ok=True)
    write_marker('ssh-revoked.json', release)
    # This final RPC connection exits naturally immediately after the caller
    # receives the result. Every other connection and listener is already gone.
    return {'ssh_access': 'revoked', **receipt(release)}

"""Shared recovery has no distro init, account policy or readiness claims."""
from launch import validate

INIT = '/sbin/init'  # Unused for launch; PID 1 uses the validated descriptor.
DIAGNOSTICS_EXECUTABLE = '/usr/sbin/dropbear'


def ready(*, ssh_revoked=False):
    return False


def validate_handoff(root):
    validate(root)


def validate_root(*args):
    raise ValueError('Shared launcher cannot install a root image')


def verify_privileges(*args):
    raise ValueError('Shared launcher cannot install a root image')


def verify_session_access(*args):
    raise ValueError('Shared launcher cannot install a root image')


def stop_diagnostics():
    raise ValueError('Recovery diagnostics are not an installed system')


def diagnostics():
    return {}

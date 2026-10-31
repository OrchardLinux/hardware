"""This computer's SSH key for installed systems: orchard's own, plus the user's.

The installer imports them into the system user's authorized_keys when the
user allows SSH logins from this computer, as an OS installer imports a key.
orchard's own key lets it check a started system; the user's lets plain
`ssh tablet@…` work.
"""
from pathlib import Path
import re
import subprocess

from .cache import cache_root
from .state import private_directory

KEY_LINE = re.compile(r'(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|ssh-rsa) [A-Za-z0-9+/=]{60,1024}')


def directory():
    return private_directory(cache_root() / 'private/ssh')


def identity():
    """orchard's private key for this computer, made once."""
    path = directory() / 'id_ed25519'
    if not path.is_file():
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'orchard', '-f', str(path)],
                       check=True, capture_output=True, timeout=30)
    return path


def public_keys():
    """The key lines to import: orchard's, then the user's own default keys."""
    keys = [' '.join(identity().with_suffix('.pub').read_text().split()[:2])]
    for name in ('id_ed25519.pub', 'id_ecdsa.pub', 'id_rsa.pub'):
        path = Path.home() / '.ssh' / name
        try:
            line = ' '.join(path.read_text().split()[:2])
        except OSError:
            continue
        if KEY_LINE.fullmatch(line) and line not in keys:
            keys.append(line)
    return keys


def ssh_argv(user, host='172.16.42.1', timeout=8):
    """SSH to an installed system with orchard's key. A new install has new host
    keys, so the host's entry is learned again, then pinned."""
    hosts = directory() / 'known_hosts'
    subprocess.run(['ssh-keygen', '-R', host, '-f', str(hosts)], capture_output=True, timeout=10)
    return ['ssh', '-i', str(identity()), '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', f'UserKnownHostsFile="{hosts}"', '-o', 'StrictHostKeyChecking=accept-new',
            '-o', 'ConnectTimeout=' + str(timeout), f'{user}@{host}']

"""Device/boot-owned private state and the single active host operation."""
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from functools import wraps
import os
from pathlib import Path
import re
import uuid

from .cache import cache_root


class EndpointUnavailable(RuntimeError):
    """The selected USB endpoint is temporarily absent, not a different identity."""


def private_directory(path: Path) -> Path:
    """No linked private-state components; credentials never inherit public modes."""
    root = cache_root() / 'private'
    relative = path.relative_to(root)
    for directory in (root, *(root.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts) + 1))):
        if directory.is_symlink():
            raise ValueError('Linked private state refused')
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    return path


@dataclass(frozen=True)
class DeviceState:
    profile: str
    ecid: int

    def __post_init__(self):
        if not isinstance(self.profile, str) or not re.fullmatch('[a-z0-9][a-z0-9._-]*', self.profile):
            raise ValueError('Invalid device profile')
        if type(self.ecid) is not int or not 0 < self.ecid < 2**64:
            raise ValueError('Invalid device ECID')

    @property
    def root(self) -> Path:
        return cache_root() / 'private/devices' / self.profile / f'{self.ecid:016x}'


@dataclass(frozen=True)
class BootState:
    device: DeviceState
    boot_id: str

    def __post_init__(self):
        try:
            parsed = uuid.UUID(self.boot_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError('Invalid device boot ID') from error
        if not parsed.int:
            raise ValueError('Invalid device boot ID')
        object.__setattr__(self, 'boot_id', str(parsed))

    @property
    def root(self) -> Path:
        return self.device.root / 'boots' / uuid.UUID(self.boot_id).hex

    def matches(self, observation) -> bool:
        return (isinstance(observation, dict) and type(observation.get('ecid')) is int and
                observation.get('ecid') == self.device.ecid and
                observation.get('boot_id') == self.boot_id)


@contextmanager
def operation_lock():
    root = private_directory(cache_root() / 'private')
    fd = os.open(root / 'operation.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another Orchard device operation is active; wait for it to finish') from error
        yield
    finally:
        os.close(fd)


def exclusive_operation(function):
    @wraps(function)
    def guarded(*args, **kwargs):
        with operation_lock():
            return function(*args, **kwargs)
    return guarded

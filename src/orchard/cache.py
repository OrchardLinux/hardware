"""Central Orchard store, independent of distro and source checkout."""
import os
from pathlib import Path
import sys


def cache_root() -> Path:
    override = os.environ.get('ORCHARD_HOME')
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Orchard'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'orchard'

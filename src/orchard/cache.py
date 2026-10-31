"""Central Orchard store, independent of distro and source checkout."""
import json
import os
from pathlib import Path
import sys

# The layout of Orchard's directories. A tool that finds a newer version
# refuses to touch them; a newer tool migrates an older layout, then records
# its own version. Raise it with every change older tools would misread.
STATE_VERSION = 1


def cache_root() -> Path:
    override = os.environ.get('ORCHARD_HOME')
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Orchard'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'orchard'


def check_state(root=None):
    """Refuse ROOT (default: the store) if a newer orchard laid it out; record this version otherwise.

    Every orchard on a host shares its directories: the user's orchard, a
    distribution's installer, development checkouts. Returns the version found
    (0 for a directory no versioned orchard has written yet).
    """
    root = Path(root) if root is not None else cache_root()
    record = root / 'state.json'
    try:
        found = json.loads(record.read_text())['version']
    except FileNotFoundError:
        found = 0
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f'{record} is unreadable ({error}); move it away and retry') from error
    if not isinstance(found, int) or found > STATE_VERSION:
        raise ValueError(f'{root} was laid out by a newer orchard (state {found}, this one knows '
                         f'{STATE_VERSION}); update orchard: orchard update')
    if found < STATE_VERSION:
        root.mkdir(parents=True, exist_ok=True)
        partial = record.with_name(f'.state-{os.getpid()}.json')
        partial.write_text(json.dumps({'version': STATE_VERSION}) + '\n')
        os.replace(partial, record)
    return found

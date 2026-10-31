"""Host-side primitives for installing verified image inputs."""
import json
from pathlib import Path


def version():
    """This orchard's version: the core's core.json, or the installed package's metadata."""
    core = Path(__file__).resolve().parents[2] / 'core.json'
    if core.is_file():
        return json.loads(core.read_text())['version']
    from importlib.metadata import version as package_version
    return package_version('orchard')

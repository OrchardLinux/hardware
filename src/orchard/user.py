"""The user's orchard: install, launch, devices, update. Standard library only.

A distribution's installer (its install.sh) runs this from the release's
orchard-install.pyz, and leaves it behind as `orchard` on PATH: `orchard
launch` starts an installed system again from any computer with Orchard.
Development uses orchard's full command (orchard.command) instead.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def bundled_release(core, origin):
    """The release a distribution's installer carries, or None.

    A local release keeps its files next to orchard-install.pyz, so its own
    index.toml is used when it is the one bundled; a published release
    refers to its files by URL.
    """
    if core is None:
        return None
    bundled = Path(core) / 'release/index.toml'
    if not bundled.is_file():
        return None
    if origin is not None:
        beside = Path(origin).parent / 'index.toml'
        if beside.is_file() and beside.read_bytes() == bundled.read_bytes():
            return beside
    return bundled


def main(argv=None, *, core=None, origin=None):
    from .cache import check_state
    from .progress import Progress
    parser = argparse.ArgumentParser(prog='orchard', description=__doc__.split('\n\n')[0])
    parser.add_argument('--verbose', action='store_true', help='show internal device events')
    commands = parser.add_subparsers(dest='action', required=True, metavar='{install,launch,devices,update,version}')
    install = commands.add_parser('install', help='install a distribution on the connected device')
    install.add_argument('source', nargs='?', help="an index (local or HTTPS) or a registered distro "
                         "(default: the release this installer carries)")
    launch = commands.add_parser('launch', help="start an installed system from the device's boot entries")
    launch.add_argument('--entry', help='boot entry id to start without asking')
    commands.add_parser('devices', help='list USB devices without modifying them')
    commands.add_parser('update', help="install the latest orchard release's core if it is newer")
    commands.add_parser('version', help="show this orchard's version")
    commands.add_parser('adopt', help=argparse.SUPPRESS)  # make this core the user's orchard
    args = parser.parse_args(argv)
    progress = Progress(verbose=args.verbose)
    try:
        check_state()
        from . import installer
        here = Path(core) if core is not None else Path(__file__).resolve().parents[2]
        if args.action == 'version':
            info = json.loads((here / 'core.json').read_text()) if (here / 'core.json').is_file() else {}
            print(info.get('version', 'development'))
            return 0
        if args.action == 'adopt':
            installer.adopt(here, sys.executable)
            return 0
        if args.action == 'update':
            installer.update()
            return 0
        if args.action == 'devices':
            from .apple import usb
            print(json.dumps(usb.devices(), indent=2))
            return 0
        from .host_setup import setup
        changed = []
        if core is not None:
            installer.adopt(here, sys.executable)  # even if this install stops, `orchard launch` stays
            from . import shell_path
            changed = [] if shell_path.on_path() else shell_path.ensure()
        setup(progress)
        if args.action == 'install':
            from .service import install as run
            source = args.source or bundled_release(core, origin)
            if source is None:
                raise ValueError('Specify what to install: orchard install INDEX_OR_DISTRO')
            run(str(source), progress=progress)
        else:
            from .launch import launch as run
            run(entry=args.entry, progress=progress)
        with progress.lock:
            progress.end_line()
        if changed:
            print('New terminals find orchard; in this one, run: export PATH="$HOME/.local/bin:$PATH"')
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.SubprocessError) as error:
        with progress.lock:
            progress.end_line()
        details = f'\nDiagnostic log: {progress.log_path}' if progress.log_path else ''
        print(f'orchard: {error}{details}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\norchard: stopped', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())

#!/usr/bin/env python3
"""Orchard: guided installation, launch and RAM builds."""
import argparse
from contextlib import ExitStack
import json
import os
import subprocess
from pathlib import Path
import sys

from importlib.metadata import version
from orchard.devices import load, choose
from orchard.progress import Progress
from orchard.releases import resolve, catalogue, register
from orchard.integrity import digest
from orchard.service import install
from orchard.state import operation_lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version=version('orchard'))
    parser.add_argument('--verbose', action='store_true', help='show internal device events')
    commands = parser.add_subparsers(dest='action', required=True)
    build = commands.add_parser('build', help='build DIRECTORY/init and optional DIRECTORY/rootfs into a RAM payload '
                                'for every board of one platform; in a distribution checkout (distro.toml), its release')
    run = commands.add_parser('run', help='build what is missing for the attached board, then boot it; in a '
                              "distribution checkout, its release's recovery, without installing")
    for command, what in ((build, 'a payload directory (init, optional rootfs/) or a distribution checkout'),
                          (run, 'the payload directory or distribution checkout')):
        command.add_argument('directory', type=Path, nargs='?', default=Path('.'), help=what + ' (default: this one)')
        command.add_argument('--version', help='distribution checkouts: the version to build '
                             '(default: dev-DATE-TIME)')
        command.add_argument('--device', help='a board; its platform is built (default: orchard.local.toml, '
                             'else the attached device)')
        command.add_argument('--platform', help='the platform to build, e.g. apple-16k')
        command.add_argument('--hardware', type=Path, help='use the hardware data and recipes of another orchard checkout')
        command.add_argument('--cache', type=Path, help='override the shared component cache')
        command.add_argument('--kernel-dir', type=Path, help='reuse a staged kernel/DTB/module tree')
        for component in ('pongo', 'busybox', 'loader'):
            command.add_argument('--' + component, type=Path, help='reuse this local component')
    cached = commands.add_parser('cache', help='list, remove or prune shared build components; no device access')
    cached.add_argument('remove', nargs='*', metavar='COMPONENT[/KEY]',
                        help='entries to delete, e.g. kernel-j71b/0bd1204e; lists entries when omitted')
    cached.add_argument('--prune', action='store_true',
                        help='keep only the most recently used entries of each component')
    cached.add_argument('--keep', type=int, default=5, help='entries per component that --prune keeps (default 5)')
    cached.add_argument('--cache', type=Path, help='override the shared component cache')
    run.add_argument('--no-build', action='store_true', help='boot the last build without rebuilding')
    run.add_argument('-y', '--yes', action='store_true',
                     help='boot without prompting, including without an interactive terminal')
    commands.add_parser('shell', help='open a root shell on the running RAM payload over USB SSH')
    execute = commands.add_parser('exec', help='run a command on the running RAM payload: orchard exec -- CMD...')
    execute.add_argument('remote_command', nargs='+', metavar='CMD')
    for action, first, second in (('push', 'local', 'remote'), ('pull', 'remote', 'local')):
        transfer = commands.add_parser(action, help=f'copy {first.upper()} to {second.upper()} '
                                       f'on the running RAM payload, checked by SHA-256')
        transfer.add_argument(first)
        transfer.add_argument(second)
        transfer.add_argument('--serial', action='store_true',
                              help='use the USB serial shell (payloads without SSH; up to 16 MiB)')
    guided = commands.add_parser('install', help='guided installation from distro name, index or manifest')
    guided.add_argument('source', nargs='?', help='registered distro, local TOML or exact HTTPS URL '
                        "(default in a distribution checkout: its last build)")
    guided.add_argument('--manifest', '--release', dest='manifest', help='explicit index or installation manifest')
    launcher = commands.add_parser('launch', help="start an installed system from the device's boot entries")
    launcher.add_argument('--entry', help='boot entry id to start without asking')
    distros = commands.add_parser('distros', help='list or register distro index locations')
    distros.add_argument('name', nargs='?')
    distros.add_argument('source', nargs='?')
    check = commands.add_parser('resolve', help='verify inputs without accessing a device')
    check.add_argument('source')
    check.add_argument('--profile', required=True)
    check.add_argument('--boot', action='store_true', help='resolve only boot assets')
    commands.add_parser('devices', help='list USB devices without modifying them')
    commands.add_parser('setup-host', help='install missing boot tools and Linux USB permissions; no device access')
    configure = commands.add_parser('setup', help='show what this host needs to build and boot, then set it up; safe to rerun')
    configure.add_argument('-y', '--yes', action='store_true', help='apply without asking')
    commands.add_parser('doctor', help='show what this host has and lacks for building and booting; changes nothing')
    boot = commands.add_parser('boot', help='boot recovery from a manifest or explicit image')
    boot.add_argument('source', nargs='?', help='registered distro, local TOML or exact HTTPS URL')
    boot.add_argument('--manifest', '--release', dest='manifest')
    boot.add_argument('--image', type=Path)
    boot.add_argument('--receipt', type=Path)
    boot.add_argument('--pongo-image', type=Path)
    adt = commands.add_parser('capture-adt', help='save a fresh recovery ADT privately')
    adt.add_argument('--output', type=Path, required=True)
    mixin = commands.add_parser('mixin', help='prepare a data-only private mixin')
    for name in ('firmware-dir', 'output'):
        mixin.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    progress = Progress(verbose=args.verbose)
    try:
        from orchard.cache import check_state
        from orchard.sources import cache_root as build_cache
        check_state()
        check_state(build_cache())
        if args.action in ('build', 'run'):
            from orchard import distro
            root = distro.find(args.directory)
            payload_only = [name for name in ('device', 'platform', 'hardware', 'cache', 'kernel_dir',
                                              'pongo', 'busybox', 'loader') if getattr(args, name) is not None]
            if root is not None:
                if payload_only:
                    raise ValueError(f'--{payload_only[0].replace("_", "-")} is for RAM payloads; '
                                     f'{root} is a distribution checkout (distro.toml)')
                if args.action == 'build':
                    distro.build(root, version=args.version)
                else:
                    if args.no_build and args.version:
                        raise ValueError('--no-build boots the last build; leave out --version')
                    result = distro.run(root, build_first=not args.no_build, version=args.version,
                                        yes=args.yes, progress=progress)
                    print(json.dumps(result, indent=2))
                return
            if args.version is not None:
                raise ValueError('--version is for distribution checkouts (distro.toml)')
        if args.action == 'build':
            from orchard.ram import build_directory
            build_directory(args.directory, hardware=args.hardware, cache=args.cache,
                            kernel=args.kernel_dir, busybox=args.busybox, loader=args.loader,
                            pongo=args.pongo, device=args.device, platform=args.platform)
            return
        if args.action in ('shell', 'exec', 'push', 'pull'):
            from orchard import ram_session
            if args.action == 'shell':
                raise SystemExit(ram_session.shell())
            if args.action == 'exec':
                command = args.remote_command[1:] if args.remote_command[0] == '--' else args.remote_command
                raise SystemExit(ram_session.run(command))
            if args.action == 'push':
                ram_session.push(args.local, args.remote, serial=args.serial)
            else:
                ram_session.pull(args.remote, args.local, serial=args.serial)
            return
        if args.action in ('setup', 'doctor'):
            from orchard import host_setup
            if args.action == 'doctor':
                raise SystemExit(host_setup.doctor())
            raise SystemExit(host_setup.setup_interactive(yes=args.yes))
        if args.action == 'cache':
            from orchard import components
            if args.prune and args.remove:
                raise ValueError('Give entries to remove or --prune, not both')
            if args.prune:
                components.prune(args.keep, args.cache)
            elif args.remove:
                components.remove(args.remove, args.cache)
            else:
                components.show(args.cache)
            return
        if args.action == 'run':
            from orchard.boot import run_directory
            run_directory(args.directory, yes=args.yes, build=not args.no_build, device=args.device,
                          platform=args.platform, hardware=args.hardware, cache=args.cache,
                          kernel=args.kernel_dir, busybox=args.busybox, loader=args.loader, pongo=args.pongo)
            return
        with ExitStack() as stack:
            if args.action in ('boot', 'capture-adt'):
                stack.enter_context(operation_lock())
            if args.action == 'install':
                source = args.manifest or args.source
                if not source:
                    from orchard import distro
                    root = distro.find(Path('.'))
                    if root is None:
                        raise ValueError('Specify a distro, index or manifest: orchard install SOURCE')
                    source = str(distro.latest(root))  # in a distribution checkout: its last build
                result = install(source, progress=progress)
            elif args.action == 'launch':
                from orchard.launch import launch
                result = launch(entry=args.entry, progress=progress)
            elif args.action == 'distros':
                if bool(args.name) != bool(args.source):
                    raise ValueError('Use orchard distros NAME INDEX_URL_OR_FILE, or orchard distros to list')
                result = register(args.name, args.source) if args.name else catalogue()
            elif args.action == 'resolve':
                record, assets = resolve(args.source, args.profile, progress, purpose='boot' if args.boot else 'install')
                result = {'manifest': record, 'files': {key: str(path) for key, path in assets.items()}}
            elif args.action == 'devices':
                from orchard.apple import usb
                result = usb.devices()
            elif args.action == 'setup-host':
                from orchard.host_setup import setup
                result = setup(progress)
            elif args.action == 'boot':
                adapter, device = choose()
                if args.image:
                    if not args.receipt:
                        raise ValueError('--image requires --receipt')
                    receipt = json.loads(args.receipt.read_text())
                    expected = receipt.get('image_sha256', receipt.get('sha256'))
                    if args.image.is_symlink() or digest(args.image) != expected:
                        raise ValueError('Explicit recovery image differs from receipt')
                    if device['mode'] == 'dfu' and not args.pongo_image:
                        raise ValueError('DFU boot with explicit files requires --pongo-image')
                    assets = {'recovery': args.image, 'pongo': args.pongo_image}
                    record = {'assets': {'recovery': {'sha256': expected}}}
                else:
                    source = args.manifest or args.source
                    if not source:
                        raise ValueError('Specify a manifest or explicit --image/--receipt inputs')
                    record, assets = resolve(source, adapter.PROFILE, progress, purpose='boot')
                adapter.boot(device, assets, record, progress)
                result = {'recovery_observed': True}
            elif args.action == 'capture-adt':
                adapter, device = choose()
                if args.output.exists() or args.output.is_symlink():
                    raise ValueError('ADT destination already exists')
                data = adapter.capture_adt(device)
                args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with os.fdopen(os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                    stream.write(data)
                result = {'saved': str(args.output), 'bytes': len(data)}
            elif args.action == 'mixin':
                adapter = load()
                archive, result = adapter.prepare_mixin(args.firmware_dir, args.output)
                result = {**result, 'path': str(archive)}
            with progress.lock:
                progress.end_line()
            if args.action not in ('install', 'launch') or not sys.stdout.isatty():
                print(json.dumps(result, indent=2))
    except (ValueError, RuntimeError, OSError, KeyError, subprocess.SubprocessError) as error:
        with progress.lock:
            progress.end_line()
        details = f'\nDiagnostic log: {progress.log_path}' if progress.log_path else ''
        parser.exit(1, f'orchard: {error}{details}\n')


if __name__ == '__main__':
    main()

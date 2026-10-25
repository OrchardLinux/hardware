"""Run the same build recipes with Podman locally or directly on native CI.

Recipes address inputs through ORCHARD_* path variables. Native mode provides
real paths; local mode provides the corresponding container mount paths. Native
mode is not a sandbox and is only for trusted, disposable Linux ARM64 workers.
"""
import argparse
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def native():
    value = os.environ.get('ORCHARD_BUILD_NATIVE', '0')
    if value not in ('0', '1'):
        raise ValueError('ORCHARD_BUILD_NATIVE must be 0 or 1')
    if value == '1':
        if platform.system() != 'Linux' or platform.machine() not in ('aarch64', 'arm64'):
            raise ValueError('Native builds require Linux ARM64')
        if not os.environ.get('ORCHARD_BUILD_ENV_ID'):
            raise ValueError('Native builds require ORCHARD_BUILD_ENV_ID')
        return True
    return False


def build(image, containerfile, context, arch=None):
    if native():
        if arch not in (None, 'arm64'):
            raise ValueError('Native builds require ARM64 inputs')
        print('Using provisioned native environment: ' + identity(image), flush=True)
    else:
        argv = ['podman', 'build'] + (['--arch', arch] if arch else [])
        subprocess.run([*argv, '-f', str(containerfile), '-t', image, str(context)], check=True)


def identity(image):
    if native():
        return 'native:' + os.environ['ORCHARD_BUILD_ENV_ID']
    return subprocess.check_output(['podman', 'image', 'inspect', '--format', '{{.Id}}', image], text=True).strip()


def path_variable(target):
    return 'ORCHARD_' + target.strip('/').replace('/', '_').upper()


def translate(value, mounts):
    for source, target, _ in sorted(mounts, key=lambda row: len(row[1]), reverse=True):
        if value == target or value.startswith(target + '/'):
            return str(Path(source).resolve()) + value[len(target):]
    return value


def run(image, command, mounts=(), *, env=None, cwd=None, root=False, network=False, arch=None):
    if native():
        if arch not in (None, 'arm64'):
            raise ValueError('Native builds require ARM64 inputs')
        with tempfile.TemporaryDirectory(prefix='orchard-recipe-') as temporary:
            work, tmp = Path(temporary) / 'work', Path(temporary) / 'tmp'
            work.mkdir()
            tmp.mkdir()
            paths = {'ORCHARD_WORK': str(work), 'ORCHARD_TMP': str(tmp)}
            paths.update({path_variable(target): str(Path(source).resolve()) for source, target, _ in mounts})
            extra = {key: translate(value, mounts) for key, value in (env or {}).items()}
            subprocess.run([translate(value, mounts) for value in command],
                           cwd=translate(cwd, mounts) if cwd else None,
                           env={**os.environ, **paths, **extra}, check=True)
    else:
        argv = ['podman', 'run', '--rm', '--userns', 'keep-id:uid=0,gid=0' if root else 'keep-id']
        if arch:
            argv += ['--arch', arch]
        if not network:
            argv += ['--network', 'none']
        paths = {'ORCHARD_WORK': '/work', 'ORCHARD_TMP': '/tmp'}
        for source, target, readonly in mounts:
            argv += ['-v', str(Path(source).resolve()) + ':' + target + (':ro' if readonly else ':rw')]
            paths[path_variable(target)] = target
        for key, value in {**paths, **(env or {})}.items():
            argv += ['-e', key + '=' + value]
        if cwd:
            argv += ['-w', cwd]
        subprocess.run([*argv, image, *command], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    image = actions.add_parser('build')
    image.add_argument('--image', required=True)
    image.add_argument('--file', required=True, type=Path)
    image.add_argument('--context', required=True, type=Path)
    image.add_argument('--arch')
    execute = actions.add_parser('run')
    execute.add_argument('--image', required=True)
    execute.add_argument('--bind', action='append', default=[])
    execute.add_argument('--env', action='append', default=[])
    execute.add_argument('--cwd')
    execute.add_argument('--root', action='store_true')
    execute.add_argument('--network', action='store_true')
    execute.add_argument('--arch')
    execute.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.action == 'build':
        build(args.image, args.file, args.context, arch=args.arch)
    else:
        mounts = []
        for value in args.bind:
            parts = value.split(':')
            if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] not in ('ro', 'rw')):
                parser.error('Expected --bind SOURCE:TARGET[:ro|rw]')
            mounts.append((parts[0], parts[1], len(parts) == 3 and parts[2] == 'ro'))
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if not command:
            parser.error('A recipe command is required')
        run(args.image, command, mounts, env=dict(value.split('=', 1) for value in args.env),
            cwd=args.cwd, root=args.root, network=args.network, arch=args.arch)


if __name__ == '__main__':
    main()

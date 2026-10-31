"""orchard's installer: the core as one file, a release's install.sh, and the user's orchard.

The core is the part of orchard that installs and launches: USB, transports,
manifests, downloads, the install layout, the boot disk and the firmware
steps, on the standard library alone (tests/test_core.py holds it to that).
A zipapp carries it with the device data, the loaders `orchard launch` starts
(Pongo, the storage-capable m1n1) and, for a distribution's installer, the
release it installs:

    orchard-install.pyz     next to a release's index.toml: core + that release
    orchard-core.pyz        an orchard release asset: core alone, for `orchard update`

A release's install.sh downloads a hash-pinned standalone Python (no system
Python, no uv) and the release's orchard-install.pyz, checks both, and runs
it. The zipapp unpacks itself once into the store (store/core/VERSION-HASH)
and leaves the user's orchard behind: `orchard` on PATH with install, launch,
devices and update. An installer replaces it only with a newer core, never an
older one, and never replaces a development orchard on PATH.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from .cache import cache_root

PACKAGE = Path(__file__).resolve().parent
# Development tooling (building kernels, payloads, distributions), not the core.
DEVELOPMENT = {'__main__.py', 'boot.py', 'build.py', 'buildenv.py', 'command.py', 'components.py', 'distro.py', 'kernel.py',
               'kernel_sdk.py', 'ram.py', 'ram_session.py', 'sources.py', 'image'}
MAX_CORE = 128 * 1024 * 1024
RELEASES = 'https://github.com/OrchardLinux/orchard/releases'
MARKER = '# orchard: the user\'s orchard'

# The standalone Python install.sh runs the installer with, per host.
PYTHON = {
    'release': '20261026', 'version': '3.14.10',
    'builds': {
        'Darwin-arm64': ('aarch64-apple-darwin', 26753457,
                         '95eb8c87d16af37c54e9765e31d8c56c0d8ffa9968ea0f704f73a9b83f84e9f9'),
        'Darwin-x86_64': ('x86_64-apple-darwin', 26943401,
                          '2fd8becd48cf0ff5f0a15df3c0db7cae7aa708dcaacf901bd30d6cdd6229370b'),
        'Linux-aarch64': ('aarch64-unknown-linux-gnu', 30635900,
                          '90f48337bc3dd4ae6eda81e001e4c981236d403b6b5a07778bbdf94ee423dc00'),
        'Linux-x86_64': ('x86_64-unknown-linux-gnu', 36776589,
                         '4d3c16c59ed0802a3d1d34d8bce504b15ceeaff39d62dadecc6a9fa803412aed'),
    },
}

BOOTSTRAP = r'''"""Orchard installer: unpacks orchard's core once into the store, then runs it."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import zipfile


def store():
    override = os.environ.get('ORCHARD_HOME')
    if override:
        return Path(override).expanduser().absolute()
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/Orchard'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'orchard'


def main():
    if sys.version_info < (3, 11):
        raise SystemExit('orchard needs Python 3.11 or newer')
    archive = Path(sys.argv[0]).resolve()
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as bundle:
        info = json.loads(bundle.read('core.json'))
        target = store() / 'core' / f"{info['version']}-{digest[:12]}"
        if not (target / 'core.json').is_file():
            for name in bundle.namelist():
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name:
                    raise SystemExit(f'{archive}: unsafe member {name}')
            partial = target.with_name(f'.{target.name}-{os.getpid()}')
            bundle.extractall(partial)
            try:
                os.replace(partial, target)
            except OSError:
                if not (target / 'core.json').is_file():
                    raise
    sys.path.insert(0, str(target / 'src'))
    from orchard.user import main as run
    raise SystemExit(run(sys.argv[1:], core=target, origin=archive))


main()
'''


def version_tuple(value):
    try:
        return tuple(int(part) for part in value.split('.'))
    except (AttributeError, ValueError):
        return ()


def core_files():
    """{archive path: source path} of the core's modules."""
    files = {}
    for path in sorted(PACKAGE.rglob('*.py')):
        relative = path.relative_to(PACKAGE)
        if relative.parts[0] in DEVELOPMENT or '__pycache__' in relative.parts:
            continue
        files['src/orchard/' + relative.as_posix()] = path
    return files


def _device_files(hardware, boards):
    files = {'features.toml': hardware / 'features.toml'}
    for board in boards:
        directory = hardware / 'devices' / board
        if not (directory / 'device.toml').is_file():
            raise ValueError(f'orchard describes no device {board}')
        for path in sorted(directory.rglob('*')):
            if path.is_symlink():
                raise ValueError(f'Device data must not contain links: {path}')
            if path.is_file() and '__pycache__' not in path.parts:
                files['devices/' + path.relative_to(hardware / 'devices').as_posix()] = path
    return files


def _revision():
    from .hardware import revision
    try:
        return revision()
    except ValueError:
        return {'commit': None, 'modified': True}


def build(output, *, loaders, boards=None, release=None, hardware=None):
    """Write the core zipapp OUTPUT; returns its {'file', 'bytes', 'sha256', 'version'}.

    LOADERS is {'pongo': Path, 'm1n1': Path}. BOARDS are the devices to
    describe (default: every one). RELEASE is a release directory whose
    index.toml (and the release.toml it pins, if a local file) it carries.
    """
    from importlib.metadata import version
    from .hardware import ROOT, profiles
    hardware = Path(hardware or ROOT)
    boards = sorted(boards) if boards is not None else profiles()
    members = {**core_files(), **_device_files(hardware, boards)}
    for name in ('pongo', 'm1n1'):
        path = Path(loaders[name])
        if path.is_symlink() or not path.is_file():
            raise ValueError(f'Missing loader {name}: {path}')
        members[f'loaders/{name}.bin'] = path
    data = {}
    if release is not None:
        from .manifests import load, resolve_local
        release = Path(release)
        index = load(release / 'index.toml')
        data['release/index.toml'] = (release / 'index.toml').read_bytes()
        if 'file' in index['release']:  # a local release; a published one fetches it by URL
            path, _ = resolve_local(index, release)
            data['release/' + index['release']['file']] = path.read_bytes()
    info = {'schema': 1, 'version': version('orchard'), 'orchard': _revision(), 'boards': boards}
    data['core.json'] = (json.dumps(info, indent=2) + '\n').encode()
    data['__main__.py'] = BOOTSTRAP.encode()
    output = Path(output)
    partial = output.with_name(f'.{output.name}-{os.getpid()}')
    with zipfile.ZipFile(partial, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in sorted({*members, *data}):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o644 << 16
            archive.writestr(entry, data[name] if name in data else members[name].read_bytes())
    os.replace(partial, output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return {'file': output.name, 'bytes': output.stat().st_size, 'sha256': digest, 'version': info['version']}


def script(*, distro, name, installer_url=None, installer_sha256):
    """install.sh of a release: standalone Python, then orchard-install.pyz, both checked, then the installer.

    INSTALLER_URL is the published orchard-install.pyz; without it the script
    runs the one next to it (a local release).
    """
    rows = '\n'.join(f'    {host}) target={target} bytes={size} sha256={checksum} ;;'
                     for host, (target, size, checksum) in PYTHON['builds'].items())
    python_url = (f'https://github.com/astral-sh/python-build-standalone/releases/download/{PYTHON["release"]}/'
                  f'cpython-{PYTHON["version"]}%2B{PYTHON["release"]}-$target-install_only_stripped.tar.gz')
    return f'''#!/bin/sh
# Install {name} with Orchard: curl -fsSL URL | sh, or sh install.sh next to a local release.
# Downloads a pinned standalone Python and this release's installer, checks
# both by SHA-256, and runs it. Generated by orchard.installer; do not edit.
set -eu
umask 022

DISTRO={distro}
INSTALLER_URL={installer_url or ''}
INSTALLER_SHA256={installer_sha256}
PYTHON_NAME=cpython-{PYTHON["version"]}+{PYTHON["release"]}

say() {{ printf '%s\\n' "$*" >&2; }}
die() {{ say "orchard: $*"; exit 1; }}
sha256() {{
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1
    else shasum -a 256 "$1" | cut -d' ' -f1; fi
}}
fetch() {{ curl -fsSL --retry 3 --proto '=https' --tlsv1.2 -o "$2" "$1"; }}

if [ -n "${{ORCHARD_HOME:-}}" ]; then store=$ORCHARD_HOME
elif [ "$(uname -s)" = Darwin ]; then store="$HOME/Library/Application Support/Orchard"
else store="${{XDG_DATA_HOME:-$HOME/.local/share}}/orchard"; fi
command -v curl >/dev/null 2>&1 || die 'install curl first'

case "$(uname -s)-$(uname -m)" in
{rows}
    Linux-arm64) target=aarch64-unknown-linux-gnu bytes=30635900 sha256={PYTHON["builds"]["Linux-aarch64"][2]} ;;
    *) die "no installer for $(uname -s) $(uname -m) yet" ;;
esac

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM
python="$store/python/$PYTHON_NAME/bin/python3"
if [ ! -x "$python" ]; then
    say "Downloading Python {PYTHON["version"]} for the installer…"
    fetch "{python_url}" "$work/python.tar.gz"
    [ "$(sha256 "$work/python.tar.gz")" = "$sha256" ] || die 'the Python download does not match its checksum'
    mkdir -p "$work/python" "$store/python"
    tar -xzf "$work/python.tar.gz" -C "$work/python"
    rm -rf "$store/python/$PYTHON_NAME"
    mv "$work/python/python" "$store/python/$PYTHON_NAME"
fi

here=
case "$0" in install.sh|*/install.sh) here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) ;; esac
if [ -n "$here" ] && [ -f "$here/orchard-install.pyz" ]; then
    installer="$here/orchard-install.pyz"
elif [ -n "$INSTALLER_URL" ]; then
    say "Downloading the $DISTRO installer…"
    installer="$work/orchard-install.pyz"
    fetch "$INSTALLER_URL" "$installer"
else
    die 'run this install.sh next to its orchard-install.pyz'
fi
[ "$(sha256 "$installer")" = "$INSTALLER_SHA256" ] || die 'the installer does not match its checksum'

if [ -t 0 ]; then
    "$python" -I "$installer" install "$@"
elif [ -r /dev/tty ] && (: < /dev/tty) 2>/dev/null; then
    "$python" -I "$installer" install "$@" < /dev/tty
else
    die 'the installer asks questions; run it in a terminal'
fi
'''


def release_manifest(directory, index):
    """The installation manifest DIRECTORY's INDEX pins: a local file, or the published one staged beside it."""
    from .manifests import ManifestError, load, resolve_local, verify_file
    if 'file' in index['release']:
        return resolve_local(index, directory)[1]
    name = index['release']['url'].rsplit('/', 1)[-1]
    path = verify_file({**{k: v for k, v in index['release'].items() if k != 'url'}, 'file': name}, directory)
    manifest = load(path)
    if manifest['kind'] != 'orchard.install' or manifest['distro'] != index['distro']:
        raise ManifestError('The staged manifest does not match its index')
    return manifest


def loaders_from(archive, directory):
    """Pongo and m1n1 out of an earlier zipapp ARCHIVE, written into DIRECTORY."""
    result = {}
    with zipfile.ZipFile(archive) as bundle:
        for name in ('pongo', 'm1n1'):
            result[name] = Path(directory) / f'{name}.bin'
            result[name].write_bytes(bundle.read(f'loaders/{name}.bin'))
    return result


def release(directory, *, loaders, installer_url=None, hardware=None):
    """Add orchard-install.pyz and install.sh to the release DIRECTORY; returns the zipapp's receipt.

    A published DIRECTORY (its index pins release.toml by URL) carries that
    release.toml beside it, as orchard.image.publish stages it.
    """
    from .manifests import load
    directory = Path(directory)
    index = load(directory / 'index.toml')
    manifest = release_manifest(directory, index)
    boards = sorted({board for row in manifest['platforms'].values() for board in row['boards']})
    receipt = build(directory / 'orchard-install.pyz', loaders=loaders, boards=boards, release=directory,
                    hardware=hardware)
    text = script(distro=manifest['distro'], name=manifest.get('name', manifest['distro']),
                  installer_url=installer_url, installer_sha256=receipt['sha256'])
    (directory / 'install.sh').write_text(text)
    (directory / 'install.sh').chmod(0o755)
    return receipt


# The user's orchard: the core in the store, `orchard` on PATH.

def current():
    """{'version', 'core'} of the user's orchard, or None."""
    try:
        value = json.loads((cache_root() / 'core/current.json').read_text())
    except (OSError, ValueError):
        return None
    return value if Path(value.get('core', '')).is_dir() else None


def adopt(core, python, *, output=print):
    """Make CORE the user's orchard unless the current one is as new; returns True if it did.

    Writes store/core/current.json and ~/.local/bin/orchard, a launcher
    running CORE with PYTHON. A file there that is not such a launcher (a
    development orchard) stays as it is.
    """
    core, python = Path(core), Path(python)
    info = json.loads((core / 'core.json').read_text())
    existing = current()
    if existing and version_tuple(existing['version']) >= version_tuple(info['version']):
        return False
    record = cache_root() / 'core/current.json'
    record.parent.mkdir(parents=True, exist_ok=True)
    partial = record.with_name(f'.current-{os.getpid()}.json')
    partial.write_text(json.dumps({'version': info['version'], 'core': str(core), 'python': str(python)},
                                  indent=2) + '\n')
    os.replace(partial, record)
    launcher = Path.home() / '.local/bin/orchard'
    if launcher.is_symlink() or (launcher.exists() and MARKER not in launcher.read_text(errors='replace')):
        output(f'Kept {launcher}: it is a development orchard, which launches too.')
        return True
    launcher.parent.mkdir(parents=True, exist_ok=True)
    source = str(core / 'src')
    text = (f'#!/bin/sh\n{MARKER} (install, launch, devices, update), orchard {info["version"]}.\n'
            f'# Written by an Orchard installer; a newer one replaces it.\n'
            f'exec {_quote(str(python))} -I -c "import sys; sys.path.insert(0, sys.argv.pop(1)); '
            f'from orchard.user import main; raise SystemExit(main())" {_quote(source)} "$@"\n')
    partial = launcher.with_name(f'.orchard-{os.getpid()}')
    partial.write_text(text)
    partial.chmod(0o755)
    os.replace(partial, launcher)
    if str(launcher.parent) not in os.environ.get('PATH', '').split(os.pathsep):
        output(f'Add {launcher.parent} to your PATH to run orchard from anywhere.')
    return True


def _quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def update(*, output=print, opener=None):
    """Fetch the latest orchard release's core and make it the user's orchard if it is newer."""
    from .http import open_url
    opener = opener or open_url
    with opener(f'{RELEASES}/latest/download/SHA256SUMS') as response:
        sums = response.read(1024 * 1024).decode()
    expected = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].removeprefix('./') == 'orchard-core.pyz':
            expected = parts[0]
    if expected is None:
        raise ValueError('The latest orchard release has no orchard-core.pyz')
    with opener(f'{RELEASES}/latest/download/orchard-core.pyz') as response:
        data = response.read(MAX_CORE + 1)
    if len(data) > MAX_CORE or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('The downloaded orchard core does not match its checksum')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        latest = json.loads(archive.read('core.json'))['version']
    existing = current()
    if existing and version_tuple(existing['version']) >= version_tuple(latest):
        output(f'orchard {existing["version"]} is up to date (latest release: {latest}).')
        return False
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / 'orchard-core.pyz'
        path.write_bytes(data)
        subprocess.run([sys.executable, '-I', str(path), 'adopt'], check=True)
    output(f'orchard {latest} installed.')
    return True


def _loaders(arguments):
    if arguments.pongo and arguments.m1n1:
        return {'pongo': arguments.pongo, 'm1n1': arguments.m1n1}
    if arguments.pongo or arguments.m1n1:
        raise ValueError('Give both --pongo and --m1n1, or neither')
    from .build import prepare_loader
    return prepare_loader(device='apple')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    actions = parser.add_subparsers(dest='action', required=True)
    for_release = actions.add_parser('release', help="add orchard-install.pyz and install.sh to a release directory")
    for_release.add_argument('directory', type=Path)
    for_release.add_argument('--installer-url', help='where orchard-install.pyz is published (default: next to install.sh)')
    alone = actions.add_parser('core', help='write orchard-core.pyz (every device, no release) for orchard update')
    alone.add_argument('output', type=Path)
    for command in (for_release, alone):
        command.add_argument('--pongo', type=Path, help='default: built or reused from the cache')
        command.add_argument('--m1n1', type=Path, help='the storage-capable m1n1; default: built or reused')
    arguments = parser.parse_args()
    loaders = _loaders(arguments)
    if arguments.action == 'release':
        result = release(arguments.directory, loaders=loaders, installer_url=arguments.installer_url)
    else:
        result = build(arguments.output, loaders=loaders)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()

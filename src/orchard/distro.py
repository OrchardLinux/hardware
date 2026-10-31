"""A distribution checkout as an orchard project: build its release, boot its recovery.

`distro.toml` at the root of a distribution's checkout makes it one:

    name = "omarchy-a"          # the distro of its index.toml
    build = "tools/build"       # its build entry, relative to the checkout

In that checkout `orchard build` runs the entry as

    ENTRY --version VERSION --output out/releases/VERSION

with orchard's Python (a `.py` entry) or directly (anything else, with
ORCHARD_PYTHON set). The entry builds what is missing and writes a release
directory: the root image, per platform a recovery and orchard's layer,
`release.toml` and the `index.toml` that pins it. orchard then checks the
index and its files and records the build in out/releases/latest.json.

`orchard run` builds, then boots that release's recovery for the attached
board from RAM, without installing; `orchard install` with no source installs
the last build. The distribution owns how its image is made; orchard owns
the release format, the device data and the device.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import tomllib

DISTRO = 'distro.toml'
LATEST = 'out/releases/latest.json'
_NAME = re.compile(r'[a-z0-9][a-z0-9._-]*')
_VERSION = re.compile(r'[0-9A-Za-z][0-9A-Za-z._+-]{0,127}')


def find(directory):
    """The distribution checkout DIRECTORY is in (the nearest distro.toml up to its repository's root), or None."""
    directory = Path(directory).expanduser().resolve()
    for parent in (directory, *directory.parents):
        if (parent / DISTRO).is_file():
            return parent
        if (parent / '.git').exists():
            break
    return None


def settings(root):
    """ROOT/distro.toml, checked: {'name': str, 'build': Path}."""
    path = Path(root) / DISTRO
    try:
        with path.open('rb') as stream:
            value = tomllib.load(stream)
    except (OSError, ValueError) as error:
        raise ValueError(f'{path}: {error}') from error
    unknown = set(value) - {'name', 'build'}
    if unknown:
        raise ValueError(f'{path}: unknown settings {", ".join(sorted(unknown))}; expected name, build')
    name, build = value.get('name'), value.get('build')
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ValueError(f'{path}: name must be the distro name of its index, e.g. "omarchy-a"')
    if (not isinstance(build, str) or not build or build.startswith('/') or '\\' in build
            or any(part in ('', '.', '..') for part in build.split('/'))):
        raise ValueError(f'{path}: build must be a path inside the checkout, e.g. "tools/build"')
    entry = Path(root) / build
    if entry.is_symlink() or not entry.is_file():
        raise ValueError(f'{path}: the build entry {build} is not a file')
    if entry.suffix != '.py' and not os.access(entry, os.X_OK):
        raise ValueError(f'{path}: the build entry {build} is not executable')
    return {'name': name, 'build': entry}


def default_version(now=None):
    """dev-YYYYMMDD-HHMMSS (UTC): a new local version for every build."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return now.strftime('dev-%Y%m%d-%H%M%S')


def build(root, *, version=None):
    """Run ROOT's build entry into out/releases/VERSION, check the release, record it; returns its index."""
    from .hardware import ROOT
    from .manifests import resolve_local, verify_local_receipts
    root = Path(root).resolve()
    distro = settings(root)
    version = version or default_version()
    if not _VERSION.fullmatch(version):
        raise ValueError('A version is letters, digits and . _ + -, starting with a letter or digit')
    output = root / 'out/releases' / version
    if output.exists() or output.is_symlink():
        raise ValueError(f'{output} exists; choose another --version')
    output.parent.mkdir(parents=True, exist_ok=True)
    entry = distro['build']
    argv = [sys.executable, str(entry)] if entry.suffix == '.py' else [str(entry)]
    argv += ['--version', version, '--output', str(output)]
    print('+', ' '.join(argv), flush=True)
    environment = {**os.environ, 'ORCHARD_PYTHON': sys.executable, 'ORCHARD_CHECKOUT': str(ROOT)}
    subprocess.run(argv, cwd=root, env=environment, check=True)
    index = output / 'index.toml'
    if not index.is_file():
        raise ValueError(f'{entry.relative_to(root)} wrote no {index.relative_to(root)}')
    from .manifests import load
    document = load(index)
    _, manifest = resolve_local(document, output)
    if manifest['distro'] != distro['name']:
        raise ValueError(f'The release is {manifest["distro"]!r}, but distro.toml names {distro["name"]!r}')
    if manifest['version'] != version:
        raise ValueError(f'The release says version {manifest["version"]!r}, but was built as {version!r}')
    verify_local_receipts(document, output)
    record = root / LATEST
    record.write_text(json.dumps({'version': version, 'index': f'{version}/index.toml'}, indent=2) + '\n')
    print(f'{distro["name"]} {version}: {index}')
    return index


def latest(root):
    """The index of ROOT's last build, from out/releases/latest.json."""
    root = Path(root).resolve()
    record = root / LATEST
    if not record.is_file():
        raise ValueError(f'No build in {root}; run orchard build')
    value = json.loads(record.read_text())
    index = record.parent / value['index']
    if not index.is_file():
        raise ValueError(f'The last build {value["version"]} is gone; run orchard build')
    return index


def run(root, *, build_first=True, version=None, yes=False, progress=None):
    """Build (unless BUILD_FIRST is false), then boot the release's recovery on the attached board from RAM.

    Nothing is installed: the recovery is the installer's system, and its
    session (USB network, SSH) is how to look around. Never retries a boot.
    """
    from .boot import confirm_boot
    from .devices import choose
    from .releases import resolve
    from .state import operation_lock
    index = build(root, version=version) if build_first else latest(root)
    with operation_lock():
        adapter, device = choose()
        record, assets = resolve(str(index), adapter.PROFILE, progress, purpose='boot')
        print(f'{record["name"]} {record["release"]}: its {record["platform"]} recovery, from RAM; nothing is installed.')
        if not yes:
            confirm_boot()
        try:
            adapter.boot(device, assets, record, progress)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, KeyboardInterrupt) as error:
            raise SystemExit(f'{error}\nStopped. Inspect device state before any new boot attempt.')
    return {'recovery_observed': True, 'release': record['release']}


def component(root, name, inputs, produce, *, artifact, extra=(), keep=2):
    """A build product of ROOT keyed by its inputs: ROOT/out/components/NAME/KEY/ARTIFACT.

    INPUTS are paths relative to ROOT (files or directories) and EXTRA more
    strings, such as other components' keys, that decide the result. With
    the same inputs the earlier result is reused; otherwise PRODUCE(path, work)
    makes ARTIFACT at path, which does not exist yet; work is a directory in
    orchard's cache for this name and key, kept between attempts. KEEP results
    per name stay. Returns (path, key).
    """
    from .build import cache_root, recipe_entries
    root = Path(root).resolve()
    if not _NAME.fullmatch(name) or '/' in artifact or artifact in ('', '.', '..'):
        raise ValueError('Invalid component name or artifact')
    entries = recipe_entries(root, inputs)
    if not entries:
        raise ValueError(f'{name}: no input files under {", ".join(inputs)}')
    key = hashlib.sha256(json.dumps({'inputs': entries, 'extra': list(extra)}, sort_keys=True).encode()).hexdigest()
    directory = root / 'out/components' / name
    target = directory / key[:16]
    if (target / artifact).exists():
        print(f'Reusing {name}: {target.relative_to(root)}', flush=True)
        os.utime(target)  # most recently used: kept first
        return target / artifact, key
    print(f'Building {name}: {target.relative_to(root)}', flush=True)
    partial = directory / f'.partial-{key[:16]}'
    if partial.exists():
        shutil.rmtree(partial)
    partial.mkdir(parents=True)
    work = cache_root() / 'work' / f'{settings(root)["name"]}-{name}' / key[:16]
    work.mkdir(parents=True, exist_ok=True)
    produce(partial / artifact, work)
    if not (partial / artifact).exists():
        raise ValueError(f'{name}: the build made no {artifact}')
    if target.exists():
        shutil.rmtree(target)
    os.replace(partial, target)
    older = sorted((path for path in directory.iterdir() if path.is_dir() and path != target
                    and not path.name.startswith('.')), key=lambda path: path.stat().st_mtime, reverse=True)
    for path in older[max(keep - 1, 0):]:
        shutil.rmtree(path)
    shutil.rmtree(work, ignore_errors=True)
    return target / artifact, key

"""User-local boot tools and Linux USB access. No device operations."""
import grp
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

from .http import open_url
from .progress import Progress
from .releases import materialize
from .transports.pongo import library_path

PALERA_RELEASE = 'v2.4'
USB_RULES = Path('/etc/udev/rules.d/70-orchard.rules')


def asset(repo, tag, name, progress):
    with open_url(f'https://api.github.com/repos/{repo}/releases/tags/{tag}') as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('GitHub release metadata exceeds size limit')
    matches = [row for row in json.loads(data)['assets'] if row['name'] == name]
    if len(matches) != 1:
        raise ValueError(f'Missing GitHub asset: {repo}/{tag}/{name}')
    row = matches[0]
    digest = row.get('digest') or ''
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
        raise ValueError('GitHub asset has no SHA-256 digest: ' + name)
    if not 0 < row['size'] <= 512 * 1024 * 1024:
        raise ValueError('GitHub asset size outside limit')
    if not row['browser_download_url'].startswith(f'https://github.com/{repo}/releases/download/{tag}/'):
        raise ValueError('Unexpected GitHub asset URL')
    return materialize({'url': row['browser_download_url'], 'bytes': row['size'],
                        'sha256': digest[7:]}, None, progress, label=name.split('_')[0].split('-')[0])


def install_tool(source, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError('Refusing to overwrite existing tool: ' + str(destination))
    with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            with source.open('rb') as stream:
                shutil.copyfileobj(stream, output)
            output.flush()
            temporary.chmod(0o755)
            # link() publishes atomically and refuses a destination that appeared meanwhile.
            os.link(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


def usb_rules(group):
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', group):
        raise ValueError('Unsupported USB access group name')
    lines = ['# Orchard: DFU, Pongo and Linux recovery only.']
    for pid in ('1227', '4141', '4142'):
        lines.append(f'SUBSYSTEM=="usb", ATTR{{idVendor}}=="05ac", ATTR{{idProduct}}=="{pid}", '
                     f'MODE="0660", GROUP="{group}", TAG+="uaccess"')
    lines.append('SUBSYSTEM=="tty", ATTRS{idVendor}=="05ac", ATTRS{idProduct}=="4142", '
                 f'MODE="0660", GROUP="{group}", TAG+="uaccess"')
    # Let Orchard reset the port of a device in DFU when it does not re-enumerate after checkm8.
    lines.append('ACTION=="add", SUBSYSTEM=="usb", ENV{DEVTYPE}=="usb_device", ATTR{idVendor}=="05ac", '
                 'ATTR{idProduct}=="1227", RUN+="/bin/sh -c \'chgrp '
                 f'{group} /sys%p/port/disable && chmod g+w /sys%p/port/disable\'"')
    return '\n'.join(lines) + '\n'


def setup(progress=None):
    progress = progress or Progress()
    system = platform.system()
    arch = {'x86_64': 'x86_64', 'amd64': 'x86_64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(platform.machine())
    if system not in ('Linux', 'Darwin') or not arch:
        raise ValueError('Supported hosts: Linux/macOS on x86_64 or ARM64')
    if os.geteuid() == 0:
        raise ValueError('Run setup-host as your normal user, not root')
    for command in ('ssh', 'ssh-keygen'):
        if not shutil.which(command):
            raise ValueError('Install OpenSSH first; missing ' + command)
    library_path()  # Check libusb without enumerating or opening any devices.
    if system == 'Linux' and (not shutil.which('udevadm') or not shutil.which('sudo')):
        raise ValueError('Linux host setup requires udevadm and sudo')
    binary = Path.home() / '.local/bin'
    binary.mkdir(parents=True, exist_ok=True)
    tools = {}
    for name in ('palera1n',):
        existing = shutil.which(name)
        if existing:
            tools[name] = existing
            continue  # Never replace a user's existing executable.
        filename = 'palera1n-macos-universal' if system == 'Darwin' else 'palera1n-linux-' + arch
        source = asset('palera1n/palera1n', PALERA_RELEASE, filename, progress)
        install_tool(source, binary / name)
        tools[name] = str(binary / name)
    if system == 'Linux':
        rules = usb_rules(grp.getgrgid(os.getgid()).gr_name)
        if not USB_RULES.exists() or USB_RULES.read_text() != rules:
            progress.phase('Installing Linux USB permissions (sudo); reconnect the device afterward')
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / '70-orchard.rules'
                path.write_text(rules)
                subprocess.run(['sudo', 'install', '-m', '644', str(path), str(USB_RULES)], check=True)
            subprocess.run(['sudo', 'udevadm', 'control', '--reload-rules'], check=True)
    return {'tools': tools, 'device_modified': False}

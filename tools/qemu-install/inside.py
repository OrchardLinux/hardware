"""Inside a privileged Alpine container: install a release onto a loop disk with orchard's
real device installer, and build a test initrd from orchard's (its init and BusyBox) with
this container's virt kernel modules. Writes disk.img, Image and initrd to /work."""
import gzip
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tarfile
import tomllib

sys.path.insert(0, '/orchard')
import install_layout as L
sys.path.insert(0, '/orchard/image')
from initramfs import write as write_initramfs

release, work = Path('/release'), Path('/work')
board, platform, devicetree = sys.argv[1:4]
manifest = tomllib.loads((release / 'release.toml').read_text())
root, layer = manifest['root'], manifest['platforms'][platform]['layer']


def newc(data):
    stream, entries = io.BytesIO(data), []
    while True:
        header = stream.read(110)
        fields = [int(header[i:i + 8], 16) for i in range(6, 110, 8)]
        name = stream.read(fields[11])[:-1].decode()
        stream.read(-(110 + fields[11]) % 4)
        body = stream.read(fields[6])
        stream.read(-fields[6] % 4)
        if name == 'TRAILER!!!':
            return entries
        entries.append((name, fields[1], body))


def test_initrd():
    """orchard's initrd with this kernel's storage and ext4 modules (orchard builds ext4 in)."""
    with tarfile.open(release / layer['file']) as archive:
        name = next(n for n in archive.getnames() if n.endswith('/initrd'))
        entries = newc(gzip.decompress(archive.extractfile(name).read()))
    kernel = next(Path('/lib/modules').iterdir())
    depends = {}
    for line in (kernel / 'modules.dep').read_text().splitlines():
        key, _, value = line.partition(':')
        depends[key] = value.split()
    by_name = {re.sub(r'\.ko(\..*)?$', '', Path(k).name).replace('_', '-'): k for k in depends}
    wanted = [m for m in ('nvme', 'crc32c-generic', 'ext4') if m in by_name]
    needed, pending = [], [by_name[m] for m in wanted]
    while pending:
        key = pending.pop()
        if key not in needed:
            needed.append(key)
            pending += depends[key]
    plain = {k: re.sub(r'\.(gz|zst|xz)$', '', k) for k in needed}
    files = {n: (m, b) for n, m, b in entries if not n.startswith('lib/modules/') and n != 'etc/orchard/modules'}
    files['etc/orchard/modules'] = (0o100644, ('\n'.join(wanted) + '\n').encode())
    base = f'lib/modules/{kernel.name}'
    for key in needed:
        path = kernel / key
        if key.endswith('.gz'):
            data = gzip.decompress(path.read_bytes())
        elif key.endswith(('.zst', '.xz')):
            data = subprocess.run(['zstd' if key.endswith('.zst') else 'xz', '-dc', str(path)],
                                  capture_output=True, check=True).stdout
        else:
            data = path.read_bytes()
        target = Path(base, plain[key])
        for parent in reversed(target.parents[:-1]):
            files.setdefault(parent.as_posix(), (0o40755, b''))
        files[target.as_posix()] = (0o100644, data)
    files[base + '/modules.dep'] = (0o100644, ''.join(
        f'{plain[k]}: {" ".join(plain[d] for d in depends[k])}\n' for k in needed).encode())
    (work / 'initrd').unlink(missing_ok=True)
    write_initramfs(files, work / 'initrd')
    # QEMU takes the raw arm64 Image, not the EFI zboot wrapper.
    image = Path('/boot/vmlinuz-virt').read_bytes()
    if image[4:8] == b'zimg':
        offset, size = struct.unpack_from('<II', image, 8)
        image = gzip.decompress(image[offset:offset + size])
    (work / 'Image').write_bytes(image)


def device(disk, *args, stdin=None):
    result = subprocess.run([sys.executable, '/orchard/install_device.py', *args], stdin=stdin,
                            env={**os.environ, 'ORCHARD_INSTALL_DISK': disk}, capture_output=True, text=True)
    events = [json.loads(line) for line in result.stdout.splitlines() if line.startswith('{')]
    for event in events:
        if event['event'] in ('step', 'finished', 'error') and event.get('state') != 'started':
            print('install:', event.get('step', event['event']), event.get('detail', ''), flush=True)
    if result.returncode:
        sys.exit(result.stderr[-2000:] or 'the installer failed')
    return events


def install():
    image = work / 'disk.img'
    image.unlink(missing_ok=True)
    subprocess.run(['truncate', '-s', '16G', str(image)], check=True)
    disk = subprocess.run(['losetup', '-f', '--show', '-b', '4096', '-P', str(image)],
                          check=True, capture_output=True, text=True).stdout.strip()
    try:
        subprocess.run(['blockdev', '--setrw', disk], check=True)
        # An APFS-like container first, then free space, as on an iPad.
        subprocess.run(['sfdisk', '-q', disk], text=True, check=True,
                       input='label: gpt\nsize=1GiB, type=7C3457EF-0000-11AA-AA11-00306543ECAC\n')
        (layout,) = [e for e in device(disk, 'inspect') if e['event'] == 'layout']
        (option,) = L.options(layout)
        Path('/run/orchard').mkdir(parents=True, exist_ok=True)
        shutil.copyfile(release / layer['file'], '/run/orchard/platform.tar.gz')
        account = manifest['install'].get('account', False)
        if account:
            secrets = Path('/run/orchard/secrets.json')
            secrets.write_text(json.dumps({'password': 'orchard test', 'pin': '424242'}))
            secrets.chmod(0o600)
        plan = L.plan(layout, option, {k: root[k] for k in ('bytes', 'sha256', 'expanded_bytes', 'compression')},
                      {'id': manifest['distro'], 'title': manifest['name'], 'options': 'rootwait',
                       'devicetree': devicetree},
                      {'path': '/run/orchard/platform.tar.gz', **{k: layer[k] for k in ('bytes', 'sha256', 'expanded_bytes')}},
                      account=account)
        Path('/run/orchard/plan.json').write_text(json.dumps(plan))
        with (release / root['file']).open('rb') as stream:
            device(disk, 'run', '/run/orchard/plan.json', stdin=stream)
        (work / 'root.json').write_text(json.dumps({'partuuid': plan['root']['partuuid']}))
    finally:
        subprocess.run(['losetup', '-d', disk])


test_initrd()
install()

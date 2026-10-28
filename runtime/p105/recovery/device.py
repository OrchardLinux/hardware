#!/usr/bin/python3
"""P105 RAM recovery RPC. Internal storage operations are absent."""
import base64
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

RUN = Path('/run/orchard')
TARGET = Path('/newroot')
CONFIG = Path('/etc/orchard/p105.json')
BOOT_ID = Path('/proc/sys/kernel/random/boot_id')
COMPATIBLE = Path('/sys/firmware/devicetree/base/compatible')
FLASH = Path('/sys/firmware/devicetree/base/soc@30000000/flash-controller@1200000/status')
MTD = Path('/sys/class/mtd')


def command(*args, timeout=15, **kwargs):
    return subprocess.run(list(args), check=True, capture_output=True, timeout=timeout, **kwargs).stdout


def atomic(path, value):
    temporary = path.with_suffix('.pending')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def config():
    path = RUN/'config.json'
    return json.loads((path if path.exists() else CONFIG).read_text())


def fence():
    if b'apple,p105ap' not in COMPATIBLE.read_bytes().split(b'\0'):
        raise ValueError('Expected P105 device tree')
    if FLASH.read_bytes().rstrip(b'\0\n') != b'disabled' or list(MTD.glob('mtd[0-9]*')):
        raise ValueError('P105 flash controller fence failed')


def identity():
    fence()
    settings = config()
    ecid = settings['ecid']
    if settings.get('profile') != 'p105' or type(ecid) is not int or not 0 < ecid < 2**64:
        raise ValueError('Invalid P105 owner identity')
    return {'profile': 'p105', 'ecid': ecid, 'boot_id': str(uuid.UUID(BOOT_ID.read_text().strip()))}


def initialize():
    if os.geteuid() != 0 or RUN.is_symlink():
        raise ValueError('Recovery identity needs protected RAM state')
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    RUN.chmod(0o700)
    atomic(RUN/'config.json', config())
    value = identity()
    atomic(RUN/'identity.json', value)
    print('ECID:%016x BOOT:%s CPID:8942 BDID:a' % (value['ecid'], value['boot_id']))


def root_type():
    rows = [line.split()[2] for line in Path('/proc/mounts').read_text().splitlines()
            if len(line.split()) >= 3 and line.split()[1] == '/']
    return rows[-1] if rows else 'unknown'


def bound(request):
    live = identity()
    if type(request.get('ecid')) is not int or any(request.get(k) != v for k, v in live.items()):
        raise ValueError('Request differs from this device and boot')
    return live


def status(_):
    transitioning = False
    try:
        live = identity()
        root = root_type()
    except FileNotFoundError as error:
        # Mount moves can briefly hide proc/sys from an already-running RPC.
        # A queued, identity-bound handoff permits a pending reply during that
        # window. Readiness still requires the live flash fence after pivot.
        if error.filename not in (str(COMPATIBLE), str(FLASH), str(BOOT_ID), '/proc/mounts'):
            raise
        queued = json.loads((RUN/'handoff.json').read_text())
        live = json.loads((RUN/'identity.json').read_text())
        if (live.get('ecid') != config()['ecid'] or live.get('profile') != 'p105' or
                any(queued.get(key) != value for key, value in live.items())):
            raise ValueError('Transition identity differs from its queued handoff')
        transitioning, root = True, 'handoff'
    failure = RUN/'failure.json'
    ready = False
    if root in ('nfs', 'nfs4'):
        result = subprocess.run(['systemctl', 'is-active', 'p105-hardware.target', 'default.target'],
                                capture_output=True, timeout=5)
        ready = result.returncode == 0
    return {**live, 'release': config()['release'], 'mode': 'handoff' if transitioning else ('nfs' if root.startswith('nfs') else 'recovery'),
            'root_filesystem': root, 'ready': ready, 'flash_controller': 'disabled',
            'failure': json.loads(failure.read_text()) if failure.exists() else None,
            'operations': ['status', 'diagnostics'] + ([] if transitioning or root.startswith('nfs') else ['handoff']),
            'capabilities': ['status', 'diagnostics'] + ([] if transitioning or root.startswith('nfs') else ['handoff-nfs'])}


def bootstrap(request):
    live = bound(request)
    if os.environ.get('SSH_CONNECTION') or root_type().startswith('nfs'):
        raise ValueError('Provision SSH from the physically attached RAM serial console')
    public = request.get('public_key', '')
    if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]{60,128}', public):
        raise ValueError('Expected one Ed25519 public key')
    session = Path('/run/recovery')
    session.mkdir(mode=0o700, exist_ok=True)
    marker = session/'bootstrap.started'
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(fd)
    folder = Path('/root/.ssh')
    folder.mkdir(mode=0o700, exist_ok=True)
    authorized = session/'authorized_keys'
    authorized.write_text(public+'\n')
    authorized.chmod(0o600)
    target = folder/'authorized_keys'
    if target.is_symlink():
        raise ValueError('Linked authorization file refused')
    target.touch(mode=0o600, exist_ok=True)
    command('mount', '--bind', str(authorized), str(target))
    key = session/'hostkey'
    command('/usr/bin/dropbearkey', '-t', 'ed25519', '-f', str(key))
    output = command('/usr/bin/dropbearkey', '-y', '-f', str(key)).decode()
    matches = re.findall(r'^ssh-ed25519 [A-Za-z0-9+/=]+', output, re.M)
    if len(matches) != 1:
        raise ValueError('Expected one recovery host key')
    with (session/'ssh.log').open('ab') as log:
        subprocess.Popen(['/usr/sbin/dropbear', '-F', '-E', '-s', '-g', '-j', '-k',
            '-P', str(session/'dropbear.pid'), '-r', str(key), '-p', '10.55.0.2:2222'],
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    deadline = time.monotonic()+8
    while not (session/'dropbear.pid').exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('RAM SSH listener did not start; inspect /run/recovery/ssh.log')
        time.sleep(0.05)
    return {**live, 'host': '10.55.0.2', 'port': 2222, 'host_key': matches[0]}


def nfs_location(value):
    if not isinstance(value, str) or not re.fullmatch(r'10\.55\.0\.1:/[A-Za-z0-9._/-]+', value) or '/..' in value or value.endswith('/'):
        raise ValueError('Expected the host USB NFS export')
    return value


def handoff(request):
    live = bound(request)
    if root_type().startswith('nfs'):
        raise ValueError('This boot has already handed off')
    source = nfs_location(request.get('nfs_root'))
    operation = str(uuid.UUID(request['operation_id']))
    record = {**live, 'operation_id': operation, 'nfs_root': source, 'requested_at': time.monotonic()}
    path = RUN/'handoff.json'
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(record, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return {**live, 'operation_id': operation, 'status': 'accepted', 'nfs_root': source}


def contained(root, relative):
    path = root/relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('NFS runtime destination escapes its root')
    return path


def copy(source, target, mode=0o755):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise ValueError('Linked runtime destination refused')
    shutil.copyfile(source, target)
    target.chmod(mode)


def prepare_pivot():
    request = json.loads((RUN/'handoff.json').read_text())
    bound(request)
    source = nfs_location(request['nfs_root'])
    if time.monotonic()-request['requested_at'] < 2:
        time.sleep(2)
    TARGET.mkdir(exist_ok=True)
    command('mount', '-t', 'nfs', '-o', 'nolock,vers=3,proto=tcp,rsize=8192,wsize=8192,retry=0,timeo=50,retrans=1',
            source, str(TARGET), timeout=30)
    try:
        if contained(TARGET, 'etc/cascadia/systemd-root').read_text().strip() != 'p105-systemd-v1':
            raise ValueError('NFS root needs the qualified P105 systemd runtime')
        header = contained(TARGET, 'usr/lib/systemd/systemd').read_bytes()[:52]
        if len(header) != 52 or header[:7] != b'\x7fELF\x01\x01\x01' or header[18:20] != b'\x28\x00':
            raise ValueError('NFS systemd must be an ARM32 executable')
        python = contained(TARGET, 'usr/bin/python3')
        if not python.is_file():
            raise ValueError('NFS diagnostics require the root Python runtime')
        command('/bin/sh', str(contained(TARGET, 'usr/libexec/p105/prepare-root')), str(TARGET), source, timeout=30)
        run = contained(TARGET, 'run')
        shutil.copytree(RUN, run/'orchard', dirs_exist_ok=True)
        shutil.copytree('/run/recovery', run/'recovery', dirs_exist_ok=True)
        diagnostics = run/'p105-orchard'
        diagnostics.mkdir(mode=0o700)
        # Carry the Alpine SSH executable and its small musl dependency closure
        # in RAM. The installed userspace keeps its own C library and SSH policy.
        shutil.copytree('/usr/libexec/orchard-p105/ssh', diagnostics, dirs_exist_ok=True)
        copy(Path(__file__), contained(TARGET, 'usr/libexec/orchard-p105/device.py'))
        wrapper = contained(TARGET, 'usr/bin/orchard-device')
        wrapper.write_text('#!/bin/sh\nexec /usr/bin/python3 /usr/libexec/orchard-p105/device.py "$@"\n')
        wrapper.chmod(0o755)
        authorized = contained(TARGET, 'root/.ssh/authorized_keys')
        authorized.parent.mkdir(mode=0o700, exist_ok=True)
        if authorized.is_symlink():
            raise ValueError('Linked installed authorization file refused')
        authorized.touch(mode=0o600, exist_ok=True)
        command('mount', '--bind', str(run/'recovery/authorized_keys'), str(authorized))
        units = run/'systemd/system'
        units.mkdir(parents=True, exist_ok=True)
        (units/'orchard-p105-ssh.service').write_text('[Unit]\nDescription=Orchard P105 boot diagnostics\n'
            'After=local-fs.target\n[Service]\nType=simple\n'
            'ExecStart=/run/p105-orchard/lib/ld-musl-armhf.so.1 --library-path /run/p105-orchard/lib '
            '/run/p105-orchard/dropbear -F -E -s -g -j -k -P /run/recovery/dropbear.pid '
            '-r /run/recovery/hostkey -p 10.55.0.2:2222\n'
            '[Install]\nWantedBy=multi-user.target\n')
        wants = units/'multi-user.target.wants'
        wants.mkdir(exist_ok=True)
        (wants/'orchard-p105-ssh.service').symlink_to('/run/systemd/system/orchard-p105-ssh.service')
    except BaseException:
        for suffix in ('root/.ssh/authorized_keys', 'tmp', 'run', ''):
            subprocess.run(['umount', str(TARGET/suffix)], capture_output=True, timeout=5)
        raise


def diagnostics(_):
    result = status({})
    result['kernel'] = os.uname().release
    result['mtd_entries'] = [p.name for p in MTD.glob('mtd[0-9]*')]
    result['handoff'] = json.loads((RUN/'handoff.json').read_text()) if (RUN/'handoff.json').exists() else None
    result['log'] = command('dmesg').decode(errors='replace')[-8192:]
    return result


def main():
    operation = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if operation == 'identity':
        initialize()
        return
    if operation == 'pivot':
        try:
            prepare_pivot()
        except BaseException as error:
            atomic(RUN/'failure.json', {'operation': 'handoff', 'error': str(error)})
            raise
        return
    try:
        request = json.loads(base64.urlsafe_b64decode(sys.argv[2])) if len(sys.argv) > 2 else {'protocol': 1}
        if request.get('protocol') != 1 or operation not in ('status', 'diagnostics', 'bootstrap', 'handoff'):
            raise ValueError('Operation unavailable in P105 recovery')
        lock = os.open(RUN/'rpc.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = {'status': status, 'diagnostics': diagnostics, 'bootstrap': bootstrap, 'handoff': handoff}[operation](request)
        finally:
            os.close(lock)
        print(json.dumps({'protocol': 1, 'event': 'result', 'operation': operation, 'result': result}), flush=True)
    except (ValueError, OSError, KeyError, subprocess.SubprocessError) as error:
        print(json.dumps({'protocol': 1, 'event': 'error', 'operation': operation, 'message': str(error)}), flush=True)
        sys.exit(1)


if __name__ == '__main__':
    main()

#!/usr/bin/python3
"""PID 1 handoff; invoked only by the recovery init loop."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

from image_policy import INIT, validate_handoff


def run(*args):
    subprocess.run(args, check=True)


def writable_root(path, root, partition, request):
    mounted = False
    try:
        run('blockdev', '--setrw', partition['path'])
        run('mount', '-t', 'ext4', '-o', 'rw,noatime', partition['path'], str(path))
        mounted = True
        init = INIT
        if request.get('launch'):
            # A dirty ext4 journal can change the earlier ro,noload view.
            from launch import validate
            receipt, init = validate(root, partition)
            if receipt != request['receipt'] or init != request['init']:
                raise RuntimeError('Launch root changed during journal replay')
        return init
    except BaseException:
        try:
            if mounted:
                run('umount', str(path))
        finally:
            run('blockdev', '--setro', partition['path'])
        raise


def main():
    if os.getpid() != 1:
        raise RuntimeError('Handoff requires PID 1')
    request = json.loads(Path('/run/orchard/handoff.json').read_text())
    partition = request['target']
    rootname = request['directory']
    block = Path('/sys/class/block') / Path(partition['path']).name
    fields = dict(line.split('=', 1) for line in (block / 'uevent').read_text().splitlines() if '=' in line)
    if fields['PARTUUID'] != partition['id'] or (block / 'ro').read_text().strip() != '1':
        raise RuntimeError('Handoff partition changed')
    if request.get('launch'):
        import storage
        if storage.record(block) != partition:
            raise RuntimeError('Launch partition identity changed')
    path = Path('/run/orchard/target')
    run('mount', '-t', 'ext4', '-o', 'ro,noload', partition['path'], str(path))
    root = path / rootname
    if root.is_symlink() or json.loads((root / '.orchard-install.json').read_text()) != request['receipt']:
        raise RuntimeError('Handoff installation changed')
    validate_handoff(root)
    run('umount', str(path))
    init = writable_root(path, root, partition, request)
    Path('/newroot').mkdir(exist_ok=True)
    run('mount', '--bind', str(root), '/newroot')
    for name in ('dev', 'proc', 'sys', 'run'):
        (Path('/newroot') / name).mkdir(exist_ok=True)
    # Kill only RAM userspace; preserve kernel hardware state and /run evidence.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for process in Path('/proc').glob('[0-9]*'):
            pid = int(process.name)
            if pid == 1:
                continue
            try:
                if (process / 'cmdline').read_bytes():
                    os.kill(pid, sig)
            except (OSError, ProcessLookupError):
                pass
        if sig == signal.SIGTERM:
            time.sleep(1)
    for name in ('dev', 'proc', 'sys', 'run'):
        run('mount', '--move', '/' + name, '/newroot/' + name)
    os.execv('/bin/busybox', ['switch_root', '/newroot', init])


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # A failed move/validation must not silently strand the host. Restore
        # aliases to already-moved pseudo-filesystems and restart ONLY key-only
        # diagnostics. Never repeat the handoff or a storage operation.
        for name in ('dev', 'proc', 'sys', 'run'):
            try:
                source, target = Path('/newroot') / name, Path('/') / name
                if source.is_mount() and not target.is_mount():
                    run('mount', '--bind', str(source), str(target))
            except Exception:
                pass
        try:
            Path('/run/orchard').mkdir(parents=True, exist_ok=True)
            Path('/run/orchard/handoff-failed').write_text(str(error))
            subprocess.Popen(['/sbin/recovery-start-ssh'], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            pass
        while True:
            time.sleep(60)

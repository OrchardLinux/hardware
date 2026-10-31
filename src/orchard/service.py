"""Guided host workflow: boot the recovery, then run install_device.py on it (see install_remote)."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

from .state import exclusive_operation, private_directory
from .progress import Clock, Progress
from .protocol import Client
from . import install_layout, install_remote as remote
from .install_account import ask_secrets
from .releases import default_manifest, resolve, select_manifest
from .devices import choose


def ask(clock, prompt, input_fn=input):
    with clock.exclude('input'):
        return input_fn(prompt).strip()


def save_report(path, report):
    # Persist commitment before handoff; a reporting/transport failure must not
    # erase evidence that the root was successfully installed.
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.install-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(report, stream, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def caveats(adapter):
    """What to know before installing: features that are not working, free-text caveats."""
    from . import hardware
    labels = hardware.features()
    groups = {'missing': 'Not working yet', 'partial': 'Partly working', 'untested': 'Untested'}
    rows = []
    for state, title in groups.items():
        names = [label for name, label in labels.items() if adapter.FEATURES.get(name) == state]
        if names:
            rows.append(f'- {title}: ' + ', '.join(names))
    lines = ['Caveats:', *rows, *(f'- {text}' for text in adapter.CAVEATS)] if rows or adapter.CAVEATS else []
    if adapter.STATUS != 'supported':
        lines.append('This device has not been tested yet, proceed at your own risk!')
    return lines


def choose_target(layout, clock, *, name='Linux', input_fn=input, output=print):
    """Pick one install target from the device's layout; replacing a partition is typed out."""
    options = install_layout.options(layout)
    if not options:
        needed = (install_layout.MIN_ROOT_BYTES + install_layout.BOOT_BYTES) / 1024**3
        raise ValueError(f'No free space or Linux partition of at least {needed:.0f} GiB. '
                         'The installer never resizes iPadOS; free space has to exist already.')
    output(f'\nWhere should {name} be installed?')
    for number, option in enumerate(options, 1):
        output(f'  {number}. {option.description}')
    single = len(options) == 1
    answer = ask(clock, 'Choose [1]: ' if single else 'Choose: ', input_fn) or ('1' if single else '')
    if not answer.isdecimal() or not 1 <= int(answer) <= len(options):
        raise ValueError('No valid target was chosen; nothing was changed')
    option = options[int(answer) - 1]
    if option.kind == 'replace':
        phrase = f'erase {option.partition["number"]}'
        if option.partition['type'] == install_layout.LINUX_DATA:
            # Installs from the previous installer were one Linux-data partition; this
            # installer writes a boot partition and an arm64 root instead.
            output('This looks like an install from the previous Orchard installer. It cannot be '
                   'converted: copy anything you want to keep off the device first.')
        output('Everything on that partition will be deleted. No backup is made.')
        if ask(clock, f'Type "{phrase}" to confirm: ', input_fn) != phrase:
            raise ValueError('Not confirmed; nothing was changed')
    return option


def entry_for(record, devicetree):
    name = record.get('name', 'Linux')
    return {'id': re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'linux', 'title': name,
            'options': record.get('cmdline', 'rootwait'), 'devicetree': devicetree}


def wait_ready(connection, ready, progress, seconds=180):
    """Wait until the started system has created READY, over the session's pinned SSH."""
    progress.step('Waiting until it is ready')
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(connection.argv + ['test -e ' + shlex.quote(ready)],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=10)
            if result.returncode == 0:
                return
            error = result.stderr.decode(errors='replace').lower()
            if 'permission denied' in error or 'host key verification failed' in error:
                # The system serves SSH but not this session's key: waiting does not help.
                raise RuntimeError('The system was installed and started, but its SSH does not accept '
                                   "this session's key, so readiness cannot be checked. "
                                   + result.stderr.decode(errors='replace').strip()[-300:])
        except subprocess.TimeoutExpired:
            pass
        time.sleep(1)
    raise RuntimeError('The system was installed and started, but did not report that it is ready. '
                       'Do not reinstall for this; start it with orchard launch to see its console.')


def revoke_ssh(connection):
    """Empty the session key the started system took over; open sessions end with this one."""
    result = subprocess.run(connection.argv + [': > /run/recovery/authorized_keys'],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
    if result.returncode:
        raise RuntimeError('The system is ready, but SSH access could not be removed; '
                           'restart the device to end this boot session')


@exclusive_operation
def install(manifest=None, *, input_fn=input, secrets_fn=ask_secrets, progress=None):
    clock, progress = Clock(), progress or Progress()
    adapter, device = choose()  # Definition of installation timing begins here.
    if not adapter.INSTALL:
        raise ValueError(f'Installing on the {adapter.NAME} is not available yet; '
                         'RAM boot is available for testing')
    if not adapter.DEVICETREE:
        raise ValueError(f'The {adapter.NAME} has no devicetree yet, so an installed system cannot boot on it')
    source = manifest or default_manifest(adapter.PROFILE)
    name = select_manifest(source, adapter.PROFILE, None, clock)[0].get('name', 'Linux')
    progress.say(f'Installing {name} on {adapter.NAME}')
    for line in caveats(adapter):
        progress.say('  ' + line)
    answer = ask(clock, 'Continue? [Y/n] ', input_fn)
    if answer.lower() not in ('', 'y', 'yes'):
        progress.say('Cancelled; nothing was downloaded or changed')
        return {'cancelled': True}
    record = assets = None
    firmware = None
    if not device['resumed']:
        progress.section('Downloading')
        with ThreadPoolExecutor(max_workers=2) as pool:
            firmware_future = pool.submit(adapter.firmware_inputs, clock, progress.task('Firmware'))
            record, assets = resolve(source, adapter.PROFILE, progress, clock=clock)
            firmware = firmware_future.result()
        progress.section('Starting the device')
        adapter.boot(device, assets, record, progress)
    progress.step('Connecting over SSH')
    with adapter.connect(device) as connection:
        private = private_directory(connection.scope.root)
        identity = {'profile': connection.scope.device.profile, 'ecid': connection.scope.device.ecid,
                    'boot_id': connection.scope.boot_id}
        status = Client(connection, progress).call('status')
        if record is None:
            record, assets = resolve(source, adapter.PROFILE, progress, clock=clock)
        assert assets is not None  # Resolution always supplies record and verified paths together.
        if status['release'] != record['release']:
            raise ValueError('Running recovery is not the selected release.\n' + adapter.REBOOT_MESSAGE)
        root = record['assets']['root']
        if 'expanded_bytes' not in root:
            raise ValueError('This release does not state its unpacked size; it cannot be installed')
        if firmware is None:
            firmware = adapter.firmware_inputs(clock, progress)
        remote.put(connection, remote.SCRIPT.read_bytes(), remote.SCRIPT.name)
        (layout,) = [e for e in remote.run(connection, ['inspect']) if e['event'] == 'layout']
        progress.done()
        option = choose_target(layout, clock, name=name, input_fn=input_fn, output=progress.say)
        # The account is what the release declares; it may ask for nothing.
        account = record.get('account')
        secrets = None
        if account and (account['password'] or account['pin']):
            with clock.exclude('input'):
                secrets = secrets_fn(account) if secrets_fn is not ask_secrets else ask_secrets(account, say=progress.say)
        progress.section('Installing ' + name)
        progress.step('Preparing the installation')
        with tempfile.TemporaryDirectory(prefix='installation-', dir=private) as directory:
            mixin, mixin_record = adapter.prepare_mixin(firmware, Path(directory) / 'mixin')
            remote.put(connection, mixin.read_bytes(), 'firmware.tar.gz')
        layer = record['assets']['layer']
        remote.put(connection, assets['layer'].read_bytes(), 'platform.tar.gz', timeout=600)
        image = {key: root[key] for key in ('bytes', 'sha256', 'expanded_bytes')}
        image['compression'] = root.get('compression', 'zstd')
        plan = install_layout.plan(layout, option, image, entry_for(record, adapter.DEVICETREE), account=account, layer={
            'path': remote.DIRECTORY + '/platform.tar.gz',
            **{key: layer[key] for key in ('bytes', 'sha256', 'expanded_bytes')}}, firmware={
            'path': remote.DIRECTORY + '/firmware.tar.gz',
            'bytes': mixin_record['bytes'], 'sha256': mixin_record['sha256']})
        remote.put(connection, json.dumps(plan).encode(), 'plan.json')
        if secrets is not None:
            remote.put(connection, json.dumps(secrets).encode(), 'secrets.json')
        secrets = None
        with assets['root'].open('rb') as source:
            events = remote.run(connection, ['run', remote.DIRECTORY + '/plan.json'],
                                source=source, size=root['bytes'], progress=progress)
        finished = [e for e in events if e.get('event') == 'finished']
        if len(finished) != 1:
            raise RuntimeError('The device did not confirm the installation; '
                               f'see {remote.DIRECTORY}/install.log on the recovery')
        report = {**identity, 'installed': True, 'release': record['release'],
                  'resumed': bool(device['resumed']), 'root': finished[0]['root'],
                  'boot': finished[0]['boot'], 'timing': clock.result()}
        path = private / 'last-install.json'
        save_report(path, report)
        # The recovery runs the same kernel the new entry boots, so it switches
        # into the new root right away: the first start needs no second DFU boot.
        # A release that declares `ready` keeps this session's SSH key until
        # the started system is ready; then the user decides whether it stays.
        ready = record.get('ready')
        progress.done('Installed')
        progress.section('Starting ' + name)
        Client(connection, progress).call('start', {'root': report['root'], 'boot': report['boot'],
                                                    'keep_ssh': bool(ready)})
        report['started'] = True
        save_report(path, report)
        if ready:
            wait_ready(connection, ready, progress)
            progress.done()
            report['ready'] = True
            try:
                answer = ask(clock, 'Keep SSH access for this boot session? [y/N] ', input_fn)
            except EOFError:
                answer = ''
            if answer.lower() in ('y', 'yes'):
                report['ssh_access'] = 'retained'
            else:
                revoke_ssh(connection)
                report['ssh_access'] = 'revoked'
        report['phases'] = progress.timings
        save_report(path, report)
    progress.done()
    progress.say(f'\n{name} is {"ready" if report.get("ready") else "starting"}. '
                 'To start it again later, put the device in DFU mode and run: orchard launch')
    return report

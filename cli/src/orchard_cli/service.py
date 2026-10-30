"""Guided host workflow: boot the recovery, then run install_device.py on it (see install_remote)."""
import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import re
import tempfile

from .state import exclusive_operation, private_directory
from .progress import Clock, Progress
from .protocol import Client
from . import install_layout, install_remote as remote
from .install_account import ask_secrets
from .releases import default_manifest, resolve
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
    import orchard_hardware
    labels = orchard_hardware.features()
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


def choose_target(layout, clock, *, input_fn=input, output=print):
    """Pick one install target from the device's layout; replacing a partition is typed out."""
    options = install_layout.options(layout)
    if not options:
        needed = (install_layout.MIN_ROOT_BYTES + install_layout.BOOT_BYTES) / 1024**3
        raise ValueError(f'No free space or Linux partition of at least {needed:.0f} GiB. '
                         'The installer never resizes iPadOS; free space has to exist already.')
    output('Where should Linux be installed?')
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


@exclusive_operation
def install(manifest=None, *, input_fn=input, secrets_fn=ask_secrets, progress=None):
    clock, progress = Clock(), progress or Progress()
    adapter, device = choose()  # Definition of installation timing begins here.
    if not adapter.INSTALL:
        raise ValueError(f'Installing on the {adapter.NAME} is not available yet; '
                         'RAM boot is available for testing')
    if not adapter.DEVICETREE:
        raise ValueError(f'The {adapter.NAME} has no devicetree yet, so an installed system cannot boot on it')
    progress.phase(f'{adapter.NAME} ({adapter.STATUS})')
    for line in caveats(adapter):
        progress.phase(line)
    answer = ask(clock, f'Install Linux on {adapter.NAME}? [Y/n] ', input_fn)
    if answer.lower() not in ('', 'y', 'yes'):
        progress.phase('Cancelled; no download or device mutation performed')
        return {'cancelled': True}
    record = assets = None
    firmware = None
    if not device['resumed']:
        progress.phase('Downloading installation files')
        with ThreadPoolExecutor(max_workers=2) as pool:
            firmware_future = pool.submit(adapter.firmware_inputs, clock, progress.task('Firmware'))
            record, assets = resolve(manifest or default_manifest(adapter.PROFILE), adapter.PROFILE, progress, clock=clock)
            firmware = firmware_future.result()
        progress.phase('Downloads verified; firmware extracted')
        adapter.boot(device, assets, record, progress)
    progress.phase('Waiting for authenticated recovery SSH')
    with adapter.connect(device) as connection:
        private = private_directory(connection.scope.root)
        identity = {'profile': connection.scope.device.profile, 'ecid': connection.scope.device.ecid,
                    'boot_id': connection.scope.boot_id}
        status = Client(connection, progress).call('status')
        if record is None:
            record, assets = resolve(manifest or default_manifest(adapter.PROFILE), adapter.PROFILE, progress, clock=clock)
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
        option = choose_target(layout, clock, input_fn=input_fn, output=progress.phase)
        with clock.exclude('input'):
            secrets = secrets_fn()
        with tempfile.TemporaryDirectory(prefix='installation-', dir=private) as directory:
            mixin, mixin_record = adapter.prepare_mixin(firmware, Path(directory) / 'mixin')
            remote.put(connection, mixin.read_bytes(), 'firmware.tar.gz')
        image = {key: root[key] for key in ('bytes', 'sha256', 'expanded_bytes')}
        image['compression'] = root.get('compression', 'zstd')
        plan = install_layout.plan(layout, option, image, entry_for(record, adapter.DEVICETREE), account=True, firmware={
            'path': remote.DIRECTORY + '/firmware.tar.gz',
            'bytes': mixin_record['bytes'], 'sha256': mixin_record['sha256']})
        remote.put(connection, json.dumps(plan).encode(), 'plan.json')
        remote.put(connection, json.dumps(secrets).encode(), 'secrets.json')
        secrets = None
        progress.phase('Installing ' + record.get('name', 'Linux') + '…')
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
        Client(connection, progress).call('start', {'root': report['root'], 'boot': report['boot']})
        report['started'] = True
        save_report(path, report)
    progress.phase(record.get('name', 'Linux') + ' is starting on the device. To start it again later, '
                   'put the device in DFU mode and run: orchard launch')
    return report

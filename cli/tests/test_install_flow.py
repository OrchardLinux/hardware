"""Guided install over a fake device that runs uploaded commands like the recovery's SSH."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from orchard_cli import install_remote
from orchard_cli.contracts import Connection
from orchard_cli.progress import Progress
from orchard_cli.service import install
from orchard_cli.state import BootState, DeviceState

BOOT = '11111111-1111-4111-8111-111111111111'
SCOPE = BootState(DeviceState('device', 1), BOOT)
APFS = '7c3457ef-0000-11aa-aa11-00306543ecac'
LINUX = '0fc63daf-8483-4772-8e79-3d69d8477de4'
GIB = 1 << 30

# Stands in for `ssh root@device COMMAND`: stores uploads, answers the script's commands.
FAKE = r'''
import json, pathlib, re, sys
store, layout, outcome = pathlib.Path(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
command = sys.argv[-1]
upload = re.fullmatch(r'umask 077 && mkdir -p /run/orchard && cat > /run/orchard/(\S+)\.part && mv .*', command)
if upload:
    (store / upload.group(1)).write_bytes(sys.stdin.buffer.read())
elif command == 'python3 /run/orchard/install_device.py inspect':
    print(json.dumps({'event': 'layout', **layout}))
elif command == 'python3 /run/orchard/install_device.py run /run/orchard/plan.json':
    (store / 'root-stream').write_bytes(sys.stdin.buffer.read())
    print(json.dumps({'event': 'step', 'step': 'check', 'state': 'done'}))
    if outcome == 'error':
        print(json.dumps({'event': 'error', 'code': 'layout-changed', 'detail': 'table changed'}))
        sys.exit(1)
    plan = json.loads((store / 'plan.json').read_text())
    print(json.dumps({'event': 'finished', 'root': plan['root']['partuuid'], 'boot': plan['boot']['partuuid']}))
else:
    sys.exit('unexpected command: ' + command)
'''


def layout(partitions, free):
    return {'disk': {'path': '/dev/nvme0n1', 'sector': 4096, 'table_sha256': 'f' * 64},
            'partitions': partitions, 'free': free}


class InstallFlow(unittest.TestCase):
    def run_install(self, disk, answers, outcome='ok'):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        root = Path(work.name)
        store = root / 'device'
        store.mkdir()
        image, mixin = root / 'rootfs', root / 'mixin.tar.gz'
        image.write_bytes(b'root-stream')
        mixin.write_bytes(b'firmware-bundle')
        adapter = Mock(NAME='Supported tablet', PROFILE='device', INSTALL=True, STATUS='supported',
                       CAVEATS=[], FEATURES={}, REBOOT_MESSAGE='reboot', DEVICETREE='t8010-j171.dtb')
        adapter.prepare_mixin.return_value = (mixin, {'bytes': 15, 'sha256': 'b' * 64})
        fake = root / 'fake.py'
        fake.write_text(FAKE)

        @contextmanager
        def connect(device):
            yield Connection([sys.executable, str(fake), str(store), json.dumps(disk), outcome], SCOPE, lambda: None)
        adapter.connect = connect
        record = {'release': '1.0', 'name': 'Omarchy', 'assets': {'root': {
            'bytes': 11, 'sha256': 'a' * 64, 'expanded_bytes': 2 * GIB, 'compression': 'zstd'}}}
        status = self.status = Mock()
        status.call.return_value = {'release': '1.0'}
        replies, prompts = iter(answers), []
        with patch('orchard_cli.service.choose', return_value=(adapter, {'mode': 'linux', 'ecid': 1, 'resumed': True})), \
             patch('orchard_cli.service.resolve', return_value=(record, {'root': image})), \
             patch('orchard_cli.service.Client', return_value=status), \
             patch('orchard_cli.state.cache_root', return_value=root / 'cache'):
            output = io.StringIO()
            result = install(input_fn=lambda text: prompts.append(text) or next(replies),
                             secrets_fn=lambda: {'password': 'long enough', 'pin': '424242'},
                             progress=Progress(output))
        return result, store, prompts, output.getvalue()

    def test_free_space_install_uploads_script_plan_secrets_and_streams_the_root(self):
        disk = layout([{'number': 1, 'start': 6, 'blocks': 3 * GIB // 4096, 'type': APFS,
                        'partuuid': '00000000-0000-4000-8000-000000000001'}],
                      [{'start': 3 * GIB // 4096 + 6, 'blocks': 20 * GIB // 4096}])
        result, store, prompts, output = self.run_install(disk, ['', ''])
        self.assertEqual(prompts, ['Install Linux on Supported tablet? [Y/n] ', 'Choose [1]: '])
        self.assertIn('free space, 20.0 GiB', output)
        self.assertEqual((store / 'install_device.py').read_bytes(), install_remote.SCRIPT.read_bytes())
        self.assertEqual((store / 'root-stream').read_bytes(), b'root-stream')
        self.assertEqual((store / 'firmware.tar.gz').read_bytes(), b'firmware-bundle')
        self.assertEqual(json.loads((store / 'secrets.json').read_text()), {'password': 'long enough', 'pin': '424242'})
        plan = json.loads((store / 'plan.json').read_text())
        self.assertTrue(plan['account'])
        self.assertNotIn('424242', json.dumps(plan))
        self.assertEqual(plan['entry'], {'id': 'omarchy', 'title': 'Omarchy', 'options': 'rootwait',
                                         'devicetree': 't8010-j171.dtb'})
        self.assertEqual(plan['firmware'], {'path': '/run/orchard/firmware.tar.gz', 'bytes': 15, 'sha256': 'b' * 64})
        self.assertEqual(result['root'], plan['root']['partuuid'])
        self.assertIn('orchard launch', output)
        self.assertTrue(result['started'])
        self.assertEqual(self.status.call.call_args.args, ('start', {'root': plan['root']['partuuid'], 'boot': plan['boot']['partuuid']}))

    def test_replacing_a_partition_must_be_typed_out(self):
        disk = layout([{'number': 1, 'start': 6, 'blocks': 3 * GIB // 4096, 'type': APFS,
                        'partuuid': '00000000-0000-4000-8000-000000000001'},
                       {'number': 2, 'start': 3 * GIB // 4096 + 256, 'blocks': 20 * GIB // 4096, 'type': LINUX,
                        'partuuid': '00000000-0000-4000-8000-000000000002', 'fs': 'ext4', 'label': 'OrchardLinux'}], [])
        with self.assertRaisesRegex(ValueError, 'Not confirmed'):
            self.run_install(disk, ['', '1', 'yes'])
        result, store, prompts, output = self.run_install(disk, ['', '1', 'erase 2'])
        self.assertEqual(prompts[-1], 'Type "erase 2" to confirm: ')
        self.assertIn('previous Orchard installer', output)
        self.assertEqual(json.loads((store / 'plan.json').read_text())['remove']['number'], 2)

    def test_device_error_is_reported_with_its_detail(self):
        disk = layout([], [{'start': 256, 'blocks': 20 * GIB // 4096}])
        with self.assertRaisesRegex(install_remote.RemoteError, 'table changed'):
            self.run_install(disk, ['', ''], outcome='error')

    def test_no_target_never_suggests_resizing(self):
        disk = layout([{'number': 1, 'start': 6, 'blocks': 30 * GIB // 4096, 'type': APFS,
                        'partuuid': '00000000-0000-4000-8000-000000000001'}], [])
        with self.assertRaisesRegex(ValueError, 'never resizes iPadOS'):
            self.run_install(disk, [''])


if __name__ == '__main__':
    unittest.main()

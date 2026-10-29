"""Validate host staging and refusal paths without mounts or device access."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

from orchard_hardware.boot.initramfs.nfs import stage

HERE = Path(stage.__file__).resolve().parent


def elf(machine=183, interpreter=False):
    blob = bytearray(120)
    blob[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<HH', blob, 16, 2, machine)
    struct.pack_into('<Q', blob, 32, 64)
    struct.pack_into('<HH', blob, 54, 56, 1)
    struct.pack_into('<I', blob, 64, 3 if interpreter else 1)
    return bytes(blob)


def identity():
    return {'schema': 1, 'kind': 'orchard.nfs-root', 'profile': 'j71b',
            'architecture': 'aarch64', 'kernel_release': '7.3.0-rc1-orchard-apple-16k',
            'root_id': '06106106-1061-4061-8061-061061061061',
            'image_sha256': 'a' * 64, 'image_receipt_sha256': 'b' * 64}


class NFSStaging(unittest.TestCase):
    def test_board_compatibility_comes_from_the_selected_device(self):
        for profile, compatible in (('j71b', 'apple,j71b'), ('j171', 'apple,j171'),
                                    ('d10', 'apple,d10')):
            root = identity()
            root['profile'] = profile
            with self.subTest(profile=profile):
                parsed, compatibles = stage.identity(json.dumps(root).encode())
                self.assertEqual(parsed['profile'], profile)
                self.assertEqual(compatibles, [compatible])

    def test_receipt_binds_exact_bytes_and_known_profile(self):
        root = identity()
        parsed, compatibles = stage.identity(json.dumps(root).encode())
        self.assertEqual(parsed, root)
        self.assertEqual(compatibles, ['apple,j71b'])
        for key, value in (('schema', True), ('kind', 'root'), ('profile', 'missing'),
                           ('architecture', 'x86_64'), ('kernel_release', 'bad\nrelease'),
                           ('root_id', '06106106106140618061061061061061'),
                           ('image_sha256', 'A' * 64), ('image_receipt_sha256', 'x')):
            candidate = copy.deepcopy(root)
            candidate[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                stage.identity(json.dumps(candidate).encode())
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            stage.identity(b'{"schema":1,"schema":1}')
        root['extra'] = 'field'
        with self.assertRaisesRegex(ValueError, 'Unexpected'):
            stage.identity(json.dumps(root).encode())

    def test_network_cannot_select_another_subnet_or_shell_syntax(self):
        network = stage.network('10.61.0.1', '10.61.0.2/30', '/n61-root', 32061)
        self.assertEqual(network['NFS_HOST_MAC'], '02:00:0a:3d:00:01')
        self.assertEqual(network['NFS_DEVICE_MAC'], '02:00:0a:3d:00:02')
        for host, address, export, port in (
                ('10.55.0.1', '10.61.0.2/30', '/n61-root', 32061),
                ('10.61.0.2', '10.61.0.2/30', '/n61-root', 32061),
                ('10.61.0.0', '10.61.0.2/30', '/n61-root', 32061),
                ('10.61.0.1', '10.61.0.3/30', '/n61-root', 32061),
                ('10.61.0.1', '10.61.0.2/24', '/n61-root', 32061),
                ('10.61.0.1', '10.61.0.2/30', '/root/../p105', 32061),
                ('10.61.0.1', '10.61.0.2/30', '/root;touch-pwned', 32061),
                ('10.61.0.1', '10.61.0.2/30', '/n61-root', 204)):
            with self.subTest(host=host, address=address, export=export, port=port), self.assertRaises(ValueError):
                stage.network(host, address, export, port)

    def test_stage_copies_standalone_runtime_and_refuses_dynamic_busybox(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            receipt, busybox, output = base / 'identity.json', base / 'busybox', base / 'ram'
            blob = (json.dumps(identity(), indent=1) + '\n').encode()
            receipt.write_bytes(blob)
            busybox.write_bytes(elf())
            result = stage.stage(receipt=receipt, busybox=busybox, output=output,
                                 server='10.61.0.1', address='10.61.0.2/30',
                                 export='/n61-root', port=32061)
            self.assertEqual((output / 'rootfs/etc/orchard/nfs-root.json').read_bytes(), blob)
            self.assertEqual((output / 'orchard.toml').read_text(), 'device = "j71b"\n')
            self.assertFalse((output / 'rootfs/bin/busybox').exists())
            self.assertEqual(result['network']['NFS_IDENTITY_SHA256'], hashlib.sha256(blob).hexdigest())
            self.assertEqual(result['network']['NFS_HANDOFF'], 'manual')
            self.assertEqual(result['network']['NFS_CLIENT_MODULES'], 'no')
            self.assertEqual(result['network']['NFS_KEEP_RESCUE'], 'no')
            for target in stage.SCRIPTS.values():
                path = output / target if target == 'init' else output / 'rootfs' / target
                self.assertTrue(os.access(path, os.X_OK), target)
            with self.assertRaisesRegex(ValueError, 'already exists'):
                stage.stage(receipt=receipt, busybox=busybox, output=output,
                            server='10.61.0.1', address='10.61.0.2/30', export='/n61-root', port=32061)
            for binary, error in ((elf(interpreter=True), 'static'), (elf(machine=62), 'AArch64')):
                busybox.write_bytes(binary)
                with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                    stage.stage(receipt=receipt, busybox=busybox, output=base / 'bad',
                                server='10.61.0.1', address='10.61.0.2/30', export='/n61-root', port=32061)
                self.assertFalse((base / 'bad').exists())

    def test_stage_copies_only_nfs_modules_and_transitive_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = identity()
            release = root['kernel_release']
            receipt, busybox, kernel = base / 'identity.json', base / 'busybox', base / 'kernel'
            receipt.write_text(json.dumps(root))
            busybox.write_bytes(elf())
            kernel.mkdir()
            (kernel / 'kernel.release').write_text(release + '\n')
            (kernel / 'Image.gz').write_bytes(b'kernel image')
            modules = kernel / 'lib/modules' / release
            modules.mkdir(parents=True)
            deps = {
                'kernel/fs/nfs/nfs.ko': ['kernel/net/sunrpc/sunrpc.ko', 'kernel/fs/lockd/lockd.ko'],
                'kernel/fs/nfs/nfsv4.ko': ['kernel/fs/nfs/nfs.ko', 'kernel/net/sunrpc/sunrpc.ko'],
                'kernel/net/sunrpc/sunrpc.ko': [],
                'kernel/fs/lockd/lockd.ko': ['kernel/fs/nfs_common/grace.ko'],
                'kernel/fs/nfs_common/grace.ko': [],
                'kernel/fs/nfs/filelayout/nfs_layout_nfsv41_files.ko': ['kernel/fs/nfs/nfsv4.ko'],
            }
            for name in deps:
                (modules / name).parent.mkdir(parents=True, exist_ok=True)
                (modules / name).write_bytes(name.encode())
            metadata = ''.join(name + ':' + ''.join(' ' + d for d in dependencies) + '\n'
                               for name, dependencies in deps.items())
            (modules / 'modules.dep').write_text(metadata)
            output = base / 'ram'
            result = stage.stage(receipt=receipt, busybox=busybox, output=output,
                                 server='10.61.0.1', address='10.61.0.2/30',
                                 export='/root', port=32061, kernel_dir=kernel,
                                 keep_rescue=True)
            expected = set(deps) - {'kernel/fs/nfs/filelayout/nfs_layout_nfsv41_files.ko'}
            self.assertEqual(set(result['kernel']['modules']), expected)
            self.assertEqual(result['network']['NFS_CLIENT_MODULES'], 'yes')
            self.assertEqual(result['network']['NFS_KEEP_RESCUE'], 'yes')
            staged = output / 'rootfs/lib/modules' / release
            self.assertEqual({p.relative_to(staged).as_posix() for p in staged.rglob('*.ko')}, expected)
            self.assertNotIn('nfs_layout', (staged / 'modules.dep').read_text())
            self.assertEqual(result['kernel']['image_sha256'], hashlib.sha256(b'kernel image').hexdigest())

            (kernel / 'kernel.release').write_text('wrong-release\n')
            with self.assertRaisesRegex(ValueError, 'release differs'):
                stage.nfs_modules(kernel, release)
            (kernel / 'kernel.release').write_text(release + '\n')
            (modules / 'kernel/fs/nfs_common/grace.ko').unlink()
            with self.assertRaisesRegex(ValueError, 'Missing kernel module'):
                stage.nfs_modules(kernel, release)
            (modules / 'modules.dep').write_text(metadata + '../outside.ko:\n')
            with self.assertRaisesRegex(ValueError, 'Unsafe kernel module path'):
                stage.nfs_modules(kernel, release)


class NFSRuntimeValidation(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'root'
        self.root.mkdir()
        self.metadata = identity()
        for name in ('etc/orchard', 'usr/lib/systemd',
                     'usr/lib/modules/' + self.metadata['kernel_release'], 'dev', 'proc', 'sys', 'run'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.receipt = self.root / 'etc/orchard/nfs-root.json'
        self.receipt.write_text(json.dumps(self.metadata) + '\n')
        self.systemd = self.root / 'usr/lib/systemd/systemd'
        self.systemd.write_bytes(elf())
        self.systemd.chmod(0o755)
        self.modules = self.root / 'usr/lib/modules' / self.metadata['kernel_release'] / 'modules.dep'
        self.modules.write_text('')
        config = self.base / 'nfs.conf'
        config.write_text("NFS_ARCHITECTURE=aarch64\nNFS_KERNEL_RELEASE=" + self.metadata['kernel_release'] +
                          "\nNFS_COMPATIBLE=apple,j71b\nNFS_IDENTITY_SHA256=" +
                          hashlib.sha256(self.receipt.read_bytes()).hexdigest() + '\n')
        self.compatible = self.base / 'compatible'
        self.compatible.write_bytes(b'apple,j71b\x00apple,t8010\x00')
        source = (HERE / 'validate-root').read_text().replace('/etc/orchard/nfs.conf', str(config))
        source = source.replace('/proc/device-tree/compatible', str(self.compatible))
        self.validator = self.base / 'validate-root'
        self.validator.write_text(source)
        self.shims = self.base / 'bin'
        self.shims.mkdir()
        # Only stat's Linux output and uname are emulated. The validator reads
        # actual files, modes, hashes and ELF bytes without privileged operations.
        shim = ('#!' + sys.executable + '\nimport os,pathlib,sys\n'
                "if pathlib.Path(sys.argv[0]).name=='stat':\n"
                " p=pathlib.Path(sys.argv[3]); print('0' if sys.argv[2]=='%u' else format(p.stat().st_mode & 0o7777,'o'))\n"
                "else: print('aarch64' if sys.argv[1]=='-m' else os.environ['TEST_KERNEL_RELEASE'])\n")
        for name in ('stat', 'uname'):
            path = self.shims / name
            path.write_text(shim)
            path.chmod(0o755)
        self.env = dict(os.environ, PATH=str(self.shims) + os.pathsep + os.environ['PATH'],
                        TEST_KERNEL_RELEASE=self.metadata['kernel_release'])
        if shutil.which('sha256sum') is None:
            path = self.shims / 'sha256sum'
            path.write_text('#!' + sys.executable + '\nimport hashlib,pathlib,sys\np=pathlib.Path(sys.argv[1]); print(hashlib.sha256(p.read_bytes()).hexdigest(),p)\n')
            path.chmod(0o755)

    def validate(self, board=False):
        return subprocess.run(['sh', str(self.validator), '--board' if board else str(self.root)],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_native_root_passes_and_mismatched_receipt_fails(self):
        self.assertEqual(self.validate().returncode, 0)
        self.metadata['image_sha256'] = 'c' * 64
        self.receipt.write_text(json.dumps(self.metadata))
        result = self.validate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('root receipt differs', result.stderr)

    def test_wrong_board_kernel_and_systemd_abi_are_refused(self):
        self.compatible.write_bytes(b'apple.j71b\x00')
        self.assertIn('device tree differs', self.validate(board=True).stderr)
        self.compatible.write_bytes(b'apple,j71b\x00')
        self.env['TEST_KERNEL_RELEASE'] = 'another-kernel'
        self.assertIn('kernel release differs', self.validate(board=True).stderr)
        self.env['TEST_KERNEL_RELEASE'] = self.metadata['kernel_release']
        self.systemd.write_bytes(elf(machine=62))
        self.assertIn('systemd ELF ABI differs', self.validate().stderr)

    def test_linked_writable_or_module_incomplete_root_is_refused(self):
        self.receipt.chmod(0o666)
        self.assertIn('group/other writes', self.validate().stderr)
        self.receipt.chmod(0o644)
        self.modules.unlink()
        self.assertIn('missing regular root file', self.validate().stderr)
        self.modules.write_text('')
        original = self.receipt.read_bytes()
        self.receipt.unlink()
        (self.base / 'linked-receipt').write_bytes(original)
        self.receipt.symlink_to(self.base / 'linked-receipt')
        self.assertIn('missing regular root file', self.validate().stderr)

    def test_handoff_requires_pid_one_before_reading_configuration(self):
        result = subprocess.run(['sh', str(HERE / 'handoff')], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires PID 1', result.stderr)

    def test_post_handoff_root_shell_is_opt_in(self):
        source = (HERE / 'handoff').read_text()
        block = source[source.index('if [ "${NFS_KEEP_RESCUE:-no}" = yes ]; then'):
                       source.index('# Keep an NFS-rooted interface')]
        target = self.base / 'systemd'
        block = block.replace('/run/systemd/system', str(target))
        unit = target / 'orchard-acm-rescue.service'
        wants = target / 'sysinit.target.wants/orchard-acm-rescue.service'
        env = dict(os.environ)
        env.pop('NFS_KEEP_RESCUE', None)
        subprocess.run(['/bin/sh', '-eu', '-c', block], env=env, check=True)
        self.assertFalse(unit.exists())
        env['NFS_KEEP_RESCUE'] = 'yes'
        subprocess.run(['/bin/sh', '-eu', '-c', block], env=env, check=True)
        self.assertIn('Restart=always', unit.read_text())
        self.assertTrue(wants.is_symlink())

    def test_handoff_mount_tracking_works_without_a_mountpoint_applet(self):
        source = (HERE / 'handoff').read_text()
        helpers = source[source.index('is_mounted() {'):source.index('failed() {')]
        before, moved = self.base / 'proc-before', self.base / 'proc-moved'
        helpers = helpers.replace('/proc/self/mountinfo', str(before))
        helpers = helpers.replace('/newroot' + str(before), str(moved))
        tools = self.base / 'awk-only'
        tools.mkdir()
        (tools / 'awk').symlink_to(shutil.which('awk'))
        env = dict(os.environ, PATH=str(tools))
        self.assertIsNone(shutil.which('mountpoint', path=env['PATH']))

        def mounted(path):
            return subprocess.run(['/bin/sh', '-c', helpers + '\nis_mounted "$1"',
                                   'mount-state-test', path], env=env, capture_output=True,
                                  text=True, timeout=5)

        before.write_text('36 25 0:6 / /dev rw - devtmpfs devtmpfs rw\n'
                          '37 25 0:7 / /newroot/sys rw - sysfs sysfs rw\n')
        self.assertEqual(mounted('/newroot/sys').returncode, 0)
        self.assertNotEqual(mounted('/sys').returncode, 0)
        self.assertNotEqual(mounted('/newroot/sy').returncode, 0)
        before.unlink()
        moved.write_text('36 25 0:6 / /newroot/dev rw - devtmpfs devtmpfs rw\n'
                         '37 25 0:7 / /newroot/proc rw - proc proc rw\n')
        self.assertEqual(mounted('/newroot/dev').returncode, 0)
        self.assertEqual(mounted('/newroot/proc').returncode, 0)
        self.assertNotEqual(mounted('/dev').returncode, 0)
        moved.unlink()
        self.assertNotEqual(mounted('/newroot/dev').returncode, 0)

    def test_failed_handoff_restarts_retired_rescue_even_if_its_pid_still_exists(self):
        source = (HERE / 'handoff').read_text()
        failure = source[source.index('failed() {'):source.index('trap failed EXIT')]
        runtime = self.base / 'runtime'
        rescue = runtime / 'rescue'
        rescue.mkdir(parents=True)
        (runtime / 'rescue.pid').write_text('42\n')
        failure = failure.replace('/run/orchard', str(runtime))
        busybox = rescue / 'busybox'
        busybox.write_text('#!/bin/sh\nprintf "started\\n" >> "$TEST_START_LOG"\n')
        busybox.chmod(0o755)
        start_log, kill_log = self.base / 'starts', self.base / 'signals'
        env = dict(os.environ, TEST_START_LOG=str(start_log), TEST_KILL_LOG=str(kill_log))
        mocks = '''
is_mounted() { return 1; }
# Every PID probe succeeds, including the retired child/zombie case.
kill() { printf '%s\\n' "$*" >> "$TEST_KILL_LOG"; return 0; }
# End the failure loop after any replacement process has recorded its start.
sleep() { wait; exit 0; }
'''
        for retired in ('false', 'true'):
            start_log.write_text('')
            kill_log.write_text('')
            script = 'set -eu\nrescue_retired=' + retired + '\n' + mocks + failure + '\nfailed\n'
            result = subprocess.run(['/bin/sh', '-c', script], env=env,
                                    capture_output=True, text=True, timeout=5)
            with self.subTest(retired=retired):
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(start_log.read_text(), 'started\n' if retired == 'true' else '')
                if retired == 'true':
                    self.assertIn('-KILL 42', kill_log.read_text())
                else:
                    self.assertEqual(kill_log.read_text().strip(), '-0 42')

    def test_all_runtime_scripts_parse_as_posix_shell(self):
        for source in stage.SCRIPTS:
            with self.subTest(script=source):
                subprocess.run(['sh', '-n', str(HERE / source)], check=True)


if __name__ == '__main__':
    unittest.main()

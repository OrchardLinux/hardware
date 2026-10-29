import os
from pathlib import Path
import subprocess
import tempfile
import unittest


INSTALLER = Path(__file__).resolve().parents[1] / 'boot/m1n1/install-toolchain'


class RustHostSelection(unittest.TestCase):
    def run_installer(self, architecture):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'uname').write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_ARCH"\n')
            (root / 'curl').write_text('#!/bin/sh\nprintf "%s\\n" "$2"\nexit 42\n')
            for command in ('uname', 'curl'):
                (root / command).chmod(0o755)
            return subprocess.run(
                ['bash', str(INSTALLER), '--rust-only'], capture_output=True, text=True,
                env={**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH'],
                     'TEST_ARCH': architecture}, check=False,
            )

    def test_selects_rust_installer_for_host(self):
        for machine, triple in [('x86_64', 'x86_64'), ('aarch64', 'aarch64'), ('arm64', 'aarch64')]:
            with self.subTest(machine=machine):
                result = self.run_installer(machine)
                self.assertEqual(result.returncode, 42)
                self.assertEqual(result.stdout.strip(),
                                 f'https://static.rust-lang.org/rustup/dist/{triple}-unknown-linux-gnu/rustup-init')

    def test_rejects_unknown_host_before_download(self):
        result = self.run_installer('riscv64')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unsupported host architecture', result.stderr)
        self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()

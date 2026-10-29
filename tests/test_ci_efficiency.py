"""Guard against rebuilding the same artifacts in a single workflow."""
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


def job(document, name):
    match = re.search(r'^  ' + re.escape(name) + r':\n(.*?)(?=^  [\w-]+:\n|\Z)', document, re.M | re.S)
    if match is None:
        raise AssertionError('Missing job: ' + name)
    return match.group(1)


class CIEfficiency(unittest.TestCase):
    def test_one_wheel_build_is_reused_by_every_consumer(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        self.assertEqual(workflow.count('python -m build'), 1)
        self.assertIn('python -m build', job(workflow, 'package'))
        self.assertNotRegex(workflow, r'pip install \.(?:\s|$)')
        for name in ('test', 'native-smoke', 'kernel', 'tools'):
            body = job(workflow, name)
            self.assertIn('name: hardware-packages', body)
            self.assertIn('pip install out/packages/*.whl', body)
        self.assertIn('name: hardware-kernel', job(workflow, 'kernel'))
        self.assertIn('name: hardware-tools', job(workflow, 'tools'))
        self.assertNotIn('kernel/build j71b', job(workflow, 'tools'))
        self.assertNotIn('build-pongo.py', job(workflow, 'assets'))

    def test_apple_build_has_exact_input_cache_and_one_compiler_invocation(self):
        body = job((ROOT / '.github/workflows/build.yml').read_text(), 'pongo')
        self.assertEqual(body.count('python boot/quiet/build-pongo.py'), 1)
        self.assertIn("if: steps.pongo-cache.outputs.cache-hit != 'true'", body)
        self.assertIn('steps.toolchain.outputs.fingerprint', body)
        self.assertIn("hashFiles('boot/quiet/build-pongo.py', 'boot/quiet/pongo.patch')", body)
        self.assertIn('xcrun --sdk iphoneos --show-sdk-build-version', body)

    def test_n61_kernel_check_is_opt_in_and_separate_from_release_assets(self):
        workflow = (ROOT / '.github/workflows/build.yml').read_text()
        body = job(workflow, 'kernel-n61-check')
        self.assertIn("github.event_name == 'workflow_dispatch' && inputs.verify_n61_kernel == true", body)
        self.assertRegex(workflow, r'verify_n61_kernel:\n(?:.*\n){3}\s+default: false')
        self.assertIn('timeout-minutes: 360', body)
        self.assertIn('bash kernel/install-toolchain', body)
        self.assertIn('bash kernel/build n61', body)
        self.assertIn('name: n61-kernel-check', body)
        self.assertIn('pattern: hardware-*', job(workflow, 'assets'))

    def test_shared_toolchain_package_lists_can_be_queried_without_installing(self):
        recipes = [('kernel/install-toolchain', 'kmod'),
                   ('boot/m1n1/install-toolchain', 'clang-19'),
                   ('boot/initramfs/recovery/install-toolchain', 'musl-tools')]
        for relative, required in recipes:
            packages = subprocess.check_output(['bash', str(ROOT / relative), '--packages'], text=True).splitlines()
            self.assertIn(required, packages)
            self.assertEqual(len(packages), len(set(packages)))
        installer = (ROOT / 'ci/install-toolchains').read_text()
        self.assertEqual(installer.count('apt-get update'), 1)
        self.assertEqual(installer.count('apt-get install'), 1)
        self.assertEqual(installer.count('--rust-only'), 1)


if __name__ == '__main__':
    unittest.main()

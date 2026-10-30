"""Release wheel URLs and fresh distro indexes; no real network/device access."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from orchard_cli import releases

spec = importlib.util.spec_from_file_location('prepare_release', Path(__file__).resolve().parents[1] / 'tools/prepare_release.py')
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


class ReleasePublication(unittest.TestCase):
    def test_requirements_use_versioned_github_wheels_with_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            wheels = []
            for package in ('orchard_cli', 'orchard_hardware'):
                wheel = root / (package + '-1.2.3-py3-none-any.whl')
                with zipfile.ZipFile(wheel, 'w') as archive:
                    archive.writestr(package + '-1.2.3.dist-info/METADATA',
                                     f'Name: {package.replace("_", "-")}\nVersion: 1.2.3\n')
                wheels.append(wheel)
            path = publication.prepare(*wheels, root / 'release')
            content = path.read_text()
            for repo, wheel in zip(('cli', 'hardware'), wheels):
                self.assertIn(f'https://github.com/OrchardLinux/{repo}/releases/download/v1.2.3/{wheel.name}', content)
                self.assertIn('#sha256=' + hashlib.sha256(wheel.read_bytes()).hexdigest(), content)
                self.assertEqual((root / 'release' / repo / wheel.name).read_bytes(), wheel.read_bytes())
            with self.assertRaises(FileExistsError):
                publication.prepare(*wheels, root / 'release')

    def test_registered_latest_index_is_refetched_but_matching_images_are_cached(self):
        url = 'https://example.invalid/latest/index.toml'
        requested = []
        payloads = {}
        def receipt(data, location):
            payloads[location] = data
            return f'url={json.dumps(location)}\nbytes={len(data)}\nsha256="{hashlib.sha256(data).hexdigest()}"\n'
        def release(number):
            base = f'https://example.invalid/v{number}'
            manifest = ('schema=1\nkind="orchard.install"\ndistro="omarchy-a"\nname="Omarchy A"\n'
                        f'version="{number}"\ndevice="j71b"\nboot_method="checkm8-pongo"\n'
                        'min_orchard="0.1.0"\nprotocol=1\n[install]\ndefault_root="/omarchy"\n')
            for role in ('root', 'boot', 'pongo'):
                manifest += f'[{role}]\n' + receipt((role + str(number)).encode(), base + '/' + role)
                if role == 'root':
                    manifest += 'compression="zstd"\nexpanded_bytes=100\nmembers=3\n'
            index = ('schema=1\nkind="orchard.index"\ndistro="omarchy-a"\n[devices.j71b.checkm8-pongo]\n' +
                     receipt(manifest.encode(), base + '/manifest.toml'))
            payloads[url] = index.encode()
        def open_url(location, **kwargs):
            requested.append(location)
            return io.BytesIO(payloads[location])
        def download(location, total, destination, **kwargs):
            requested.append(location)
            Path(destination).write_bytes(payloads[location])
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'ORCHARD_HOME': str(Path(directory).resolve())}), \
             patch.object(releases, 'open_url', side_effect=open_url), \
             patch.object(releases, 'download', side_effect=download):
            releases.register('omarchy-a', url)
            release(1)
            first, _ = releases.resolve('omarchy-a', 'j71b')
            releases.resolve('omarchy-a', 'j71b')
            self.assertEqual(requested.count(url), 2)
            self.assertEqual(requested.count('https://example.invalid/v1/root'), 1)
            release(2)
            second, _ = releases.resolve('omarchy-a', 'j71b')
            self.assertEqual((first['release'], second['release']), ('1', '2'))
            self.assertEqual(releases.catalogue()['omarchy-a'], url)
            self.assertEqual(requested.count('https://example.invalid/v2/root'), 1)


if __name__ == '__main__':
    unittest.main()

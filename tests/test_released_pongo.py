"""An unchanged Pongo recipe uses the released binary: no Xcode, and it works on Linux."""
import contextlib
import hashlib
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from orchard import build, http

PONGO = b'released pongo'
SUMS = f'{hashlib.sha256(PONGO).hexdigest()}  ./pongo.bin\n'.encode()


class ReleasedPongo(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.hardware = self.root / 'orchard'
        (self.hardware / 'boot/quiet').mkdir(parents=True)
        (self.hardware / 'boot/quiet/build-pongo.py').write_text('recipe\n')
        self.git('init', '-q')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', '-c', 'commit.gpgsign=false',
                 'commit', '-qm', 'release')
        self.git('tag', 'v1.0.0')
        self.fetched = []

    def git(self, *arguments):
        subprocess.run(['git', '-C', str(self.hardware), *arguments], check=True, capture_output=True)

    def open_url(self, url):
        self.fetched.append(url)
        if url.endswith('/SHA256SUMS'):
            return io.BytesIO(SUMS)
        if url.endswith('/pongo.bin'):
            return io.BytesIO(PONGO)
        raise OSError('not found: ' + url)

    def test_unchanged_recipe_uses_the_release(self):
        with patch.object(http, 'open_url', side_effect=self.open_url), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(build.released_pongo(self.hardware), ('v1.0.0', hashlib.sha256(PONGO).hexdigest()))
            pongo = build._prepare_pongo(self.hardware, self.root / 'components')
        self.assertEqual(pongo.read_bytes(), PONGO)
        self.assertTrue(self.fetched[-1].endswith('/v1.0.0/pongo.bin'))

    def test_a_changed_recipe_is_built_and_says_what_that_needs(self):
        (self.hardware / 'boot/quiet/build-pongo.py').write_text('changed\n')
        with patch.object(http, 'open_url', side_effect=self.open_url):
            self.assertIsNone(build.released_pongo(self.hardware))
        with patch.object(build.sys, 'platform', 'linux'), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, 'differs from the latest release.*macOS with Xcode'):
            build._prepare_pongo(self.hardware, self.root / 'components')
        self.assertEqual(self.fetched, [])

    def test_a_tampered_download_is_refused(self):
        def tampered(url):
            return io.BytesIO(b'other bytes') if url.endswith('/pongo.bin') else self.open_url(url)
        with patch.object(http, 'open_url', side_effect=tampered), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaisesRegex(ValueError, 'does not match its checksum'):
            build._prepare_pongo(self.hardware, self.root / 'components')

    def test_no_release_tag_or_no_network_builds_locally(self):
        self.git('tag', '-d', 'v1.0.0')
        self.assertIsNone(build.released_pongo(self.hardware))
        self.git('tag', 'v1.0.0')
        with patch.object(http, 'open_url', side_effect=OSError('offline')):
            self.assertIsNone(build.released_pongo(self.hardware))


if __name__ == '__main__':
    unittest.main()

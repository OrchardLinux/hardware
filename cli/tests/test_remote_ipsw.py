"""Byte-range IPSW reads against an in-memory server; no network."""
import io
import struct
import unittest
from unittest.mock import patch
import zipfile

from orchard_cli.apple import lzfse, remote_ipsw


class Response(io.BytesIO):
    def __init__(self, data, status, headers):
        super().__init__(data)
        self.status, self.headers = status, headers

    def __exit__(self, *exc):
        self.close()


def server(blob, requests):
    def open_url(request):
        requests.append(request.get_header('Range'))
        if request.get_method() == 'HEAD':
            return Response(b'', 200, {'Content-Length': str(len(blob))})
        first, last = map(int, request.get_header('Range')[6:].split('-'))
        return Response(blob[first:last + 1], 206, {'Content-Range': f'bytes {first}-{last}/{len(blob)}'})
    return open_url


def ipsw(members):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        for name, (data, method) in members.items():
            archive.writestr(name, data, compress_type=method)
    return stream.getvalue()


def im4p(payload):
    def der(tag, body):
        size = len(body)
        length = bytes([size]) if size < 0x80 else bytes([0x84]) + size.to_bytes(4, 'big')
        return bytes([tag]) + length + body
    return der(0x30, der(0x16, b'IM4P') + der(0x16, b'krnl') + der(0x16, b'test') + der(0x04, payload))


class RemoteIpsw(unittest.TestCase):
    def test_members_come_from_ranges_and_are_size_checked(self):
        blob = ipsw({'BuildManifest.plist': (b'manifest' * 100, zipfile.ZIP_DEFLATED),
                     'system.dmg': (b'stored bytes', zipfile.ZIP_STORED)})
        requests = []
        with patch.object(remote_ipsw, 'open_url', server(blob, requests)):
            remote = remote_ipsw.Ipsw('https://example.invalid/x.ipsw', len(blob))
            self.assertEqual(remote.member('BuildManifest.plist'), b'manifest' * 100)
            member = remote_ipsw.Member(remote.archive, 'system.dmg')
            self.assertEqual(member.read(7, 5), b'bytes')
        self.assertIsNone(requests[0])  # HEAD first, then only ranges
        self.assertTrue(all(r.startswith('bytes=') for r in requests[1:]))

    def test_explore_lists_and_saves_members(self):
        import contextlib
        import tempfile
        from pathlib import Path
        blob = ipsw({'BuildManifest.plist': (b'manifest', zipfile.ZIP_DEFLATED),
                     'kernelcache.release.x': (b'k' * 10, zipfile.ZIP_STORED)})
        out = io.StringIO()
        with patch.object(remote_ipsw, 'open_url', server(blob, [])), contextlib.redirect_stdout(out), \
                tempfile.TemporaryDirectory() as directory:
            remote_ipsw.main(['https://example.invalid/x.ipsw', 'members'])
            target = Path(directory) / 'manifest'
            remote_ipsw.main(['https://example.invalid/x.ipsw', 'member', 'BuildManifest.plist', str(target)])
            self.assertEqual(target.read_bytes(), b'manifest')
        self.assertIn('          10  kernelcache.release.x', out.getvalue())
        self.assertIn('8 bytes  sha256 ', out.getvalue())

    def test_size_differing_from_recipe_is_refused(self):
        blob = ipsw({'a': (b'a', zipfile.ZIP_STORED)})
        with patch.object(remote_ipsw, 'open_url', server(blob, [])):
            with self.assertRaisesRegex(ValueError, 'size differs'):
                remote_ipsw.Ipsw('https://example.invalid/x.ipsw', len(blob) + 1)

    def test_ignored_range_is_refused(self):
        blob = ipsw({'a': (b'a', zipfile.ZIP_STORED)})

        def whole_file(request):
            if request.get_method() == 'HEAD':
                return Response(b'', 200, {'Content-Length': str(len(blob))})
            return Response(blob, 200, {})
        with patch.object(remote_ipsw, 'open_url', whole_file):
            with self.assertRaisesRegex(ValueError, 'byte range'):
                remote_ipsw.Ipsw('https://example.invalid/x.ipsw', len(blob))

    def test_kernelcache_im4p_is_unwrapped_to_the_macho(self):
        macho = b'\xcf\xfa\xed\xfe' + bytes(60)
        raw = b'bvx-' + struct.pack('<I', len(macho)) + macho + b'bvx$'
        self.assertEqual(remote_ipsw.im4p_macho(im4p(raw)), macho)
        fat = (b'\xca\xfe\xba\xbe' + struct.pack('>I', 1) + bytes(8) + struct.pack('>II', 28, len(macho)) +
               bytes(4) + macho)
        wrapped = b'bvx-' + struct.pack('<I', len(fat)) + fat + b'bvx$'
        self.assertEqual(remote_ipsw.im4p_macho(im4p(wrapped)), macho)
        self.assertEqual(remote_ipsw.im4p_macho(im4p(macho)), macho)  # stored uncompressed
        with self.assertRaisesRegex(ValueError, 'not a 64-bit Mach-O'):
            remote_ipsw.im4p_macho(im4p(b'dtre' + bytes(60)))

    def test_lzss_kernelcache_decodes_literals_and_back_references(self):
        import zlib
        from orchard_cli.apple import lzss
        # Flags 0xFF: eight literals. Then flags 0x00: one reference to ring
        # position 0xFEE (where the first literal landed), length 3 + 2 = 5.
        stream = b'\xff' + b'abcdefgh' + b'\x00' + bytes([0xEE, 0xF3])
        expected = b'abcdefgh' + b'abcde'
        container = (b'complzss' + struct.pack('>III', zlib.adler32(expected), len(expected), len(stream))
                     ).ljust(lzss.HEADER, b'\0') + stream
        self.assertEqual(lzss.decompress(container), expected)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            lzss.decompress(container[:8] + b'\0\0\0\0' + container[12:])

    def test_lzvn_block_decodes(self):
        # One literal run of three bytes (0xE3), then the end-of-stream opcode.
        stream = b'bvxn' + struct.pack('<II', 3, 12) + b'\xe3abc\x06' + bytes(7) + b'bvx$'
        self.assertEqual(lzfse.decompress(stream), b'abc')


if __name__ == '__main__':
    unittest.main()

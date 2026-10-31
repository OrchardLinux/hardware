import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.request

from orchard import downloads, http


class Response(io.BytesIO):
    status = 206
    def __init__(self, data, start, end, total):
        super().__init__(data)
        self.headers={'Content-Range':f'bytes {start}-{end}/{total}','Content-Length':str(len(data))}


class Downloads(unittest.TestCase):
    def test_parallel_range_resume_assembles_exact_contiguous_content(self):
        data=bytes(range(80))
        def open_url(request,timeout):
            self.assertEqual(timeout,30)
            start,end=map(int,request.get_header('Range').removeprefix('bytes=').split('-'))
            return Response(data[start:end+1],start,end,len(data))
        with tempfile.TemporaryDirectory() as directory:
            dest=Path(directory)/'artifact'
            dest.write_bytes(data[:13])
            with patch.object(downloads,'open_url',side_effect=open_url), patch('builtins.print'):
                downloads.download('https://example.invalid/fw',len(data),dest,workers=2,chunk_size=8)
            self.assertEqual(dest.read_bytes(),data)
            self.assertEqual(list((Path(directory)/'artifact.parts').glob('*.part')),[])

    def test_wrong_range_is_never_promoted(self):
        with tempfile.TemporaryDirectory() as directory:
            response=Response(b'wrong',1,5,5)
            with patch.object(downloads,'open_url',return_value=response):
                with self.assertRaisesRegex(ValueError,'exact range'):
                    downloads.fetch_chunk('https://example.invalid/fw',0,4,5,Path(directory),attempts=1)
            self.assertEqual(list(Path(directory).glob('*.part')),[])

    def test_initial_and_redirect_urls_share_strict_https_policy(self):
        for url in ('http://example.invalid/file','https://user:password@example.invalid/file','https://example.invalid/file#fragment'):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                dest=Path(directory)/'artifact'
                with self.assertRaises(ValueError):downloads.download(url,5,dest)
                self.assertFalse(dest.exists())
        request=urllib.request.Request('https://example.invalid/file',headers={'Range':'bytes=0-4'})
        with self.assertRaises(ValueError):
            http.HTTPSRedirect().redirect_request(request,None,302,'redirect',{},'http://example.invalid/file')

    def test_request_headers_are_preserved_and_final_downgrade_is_rejected(self):
        request=urllib.request.Request('https://example.invalid/file',headers={'Range':'bytes=0-4'})
        response=Mock()
        response.geturl.return_value='http://example.invalid/file'
        opener=Mock();opener.open.return_value=response
        with patch.object(http.urllib.request,'build_opener',return_value=opener):
            with self.assertRaises(ValueError):http.open_url(request,timeout=30)
        opener.open.assert_called_once_with(request,timeout=30)
        self.assertEqual(request.get_header('Range'),'bytes=0-4')
        response.close.assert_called_once()


if __name__ == '__main__':unittest.main()

#!/usr/bin/env python3
"""Bounded parallel HTTP ranges appended to an existing contiguous file prefix.

Completed chunks survive failure. HTTP range/size checks are mandatory, but the
caller MUST verify the final file's trusted full hash before using the archive.
"""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import os
from pathlib import Path
import time
import urllib.request

from .http import open_url, validate_url


def ranges(start, total, chunk_size):
    if not 0 <= start <= total or chunk_size <= 0:
        raise ValueError('Invalid download bounds')
    return [(p, min(p + chunk_size, total) - 1) for p in range(start, total, chunk_size)]


def check_response(response, start, end, total):
    expected = f'bytes {start}-{end}/{total}'
    if response.status != 206 or response.headers.get('Content-Range') != expected:
        raise ValueError(f'Server did not honor exact range {expected}')
    encoding = response.headers.get('Content-Encoding', 'identity')
    if encoding != 'identity':
        raise ValueError('Unexpected content encoding')
    length = response.headers.get('Content-Length')
    if length is not None and int(length) != end-start+1:
        raise ValueError('Wrong range Content-Length')


def fetch_chunk(url, start, end, total, directory, attempts=6):
    path = directory / f'{start}-{end}.part'
    temporary = directory / f'{start}-{end}.tmp'
    size = end-start+1
    if path.is_file() and path.stat().st_size == size:
        return path
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={
                'Range': f'bytes={start}-{end}', 'Accept-Encoding': 'identity',
                'User-Agent': 'orchard-firmware/1'})
            started = time.monotonic()
            with open_url(request, timeout=30) as response:
                check_response(response, start, end, total)
                with temporary.open('wb') as out:
                    received = 0
                    while received < size:
                        if time.monotonic()-started > 90:
                            raise TimeoutError('Chunk exceeded 90-second transfer budget')
                        data = response.read(min(1024*1024, size-received))
                        if not data:
                            raise IOError('Server closed chunk early')
                        out.write(data)
                        received += len(data)
                    out.flush()
                    os.fsync(out.fileno())
            if temporary.stat().st_size != size:
                raise ValueError('Short chunk')
            temporary.replace(path)
            return path
        except Exception:
            if attempt+1 == attempts:
                raise
            time.sleep(min(2**attempt, 16))
    raise RuntimeError('Unreachable')


def append_chunk(destination, chunk, start, expected_size):
    if destination.stat().st_size != start or chunk.stat().st_size != expected_size:
        raise ValueError('Noncontiguous assembly or wrong chunk size')
    with destination.open('ab') as out, chunk.open('rb') as source:
        while True:
            data = source.read(1024*1024)
            if not data:
                break
            out.write(data)
        out.flush()
        os.fsync(out.fileno())
    if destination.stat().st_size != start+expected_size:
        raise ValueError('Assembly size mismatch')
    chunk.unlink()


def download(url, total, destination, workers=4, chunk_size=16*1024*1024, progress=None):
    destination = Path(destination)
    validate_url(url)
    if workers not in range(1, 9):
        raise ValueError('1–8 workers required')
    if destination.is_symlink():
        raise ValueError('Refusing symlink destination')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.with_suffix(destination.suffix+'.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not destination.exists():
            destination.touch(exist_ok=False)
        if not destination.is_file():
            raise ValueError('Destination is not a regular file')
        prefix = destination.stat().st_size
        plan = ranges(prefix, total, chunk_size)
        if not plan:
            return
        directory = destination.with_suffix(destination.suffix+'.parts')
        directory.mkdir(exist_ok=True)
        pool = ThreadPoolExecutor(max_workers=workers)
        futures = {pool.submit(fetch_chunk, url, a, b, total, directory): (a, b) for a, b in plan}
        pending = set(futures)
        ready = {}
        next_byte = prefix
        next_report = 0
        if progress:
            progress({'assembled_bytes': prefix, 'completed_bytes': prefix,
                      'total_bytes': total, 'workers': workers, 'chunk_bytes': chunk_size})
        try:
            while pending:
                done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    a, b = futures[future]
                    ready[a] = (future.result(), b-a+1)
                while next_byte in ready:
                    chunk, length = ready.pop(next_byte)
                    append_chunk(destination, chunk, next_byte, length)
                    next_byte += length
                if time.monotonic() >= next_report or not pending:
                    received = next_byte + sum(length for _, length in ready.values())
                    if progress:
                        progress({'assembled_bytes': next_byte, 'completed_bytes': received,
                                  'total_bytes': total, 'workers': workers, 'chunk_bytes': chunk_size})
                    next_report = time.monotonic()+0.2
            if next_byte != total or destination.stat().st_size != total:
                raise ValueError('Incomplete assembly')
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

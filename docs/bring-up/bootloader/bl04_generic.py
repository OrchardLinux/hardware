#!/usr/bin/env python3
"""BL-04: read the GPT through m1n1's generic main_storage proxy ops only.

This is what a device-independent host (the Orchard CLI) will do: no
register knowledge on the host. Read-only; the backend has no write path.
"""
import os, pathlib, struct, sys, time, uuid
sys.path.append(os.environ.get('M1N1_PROXYCLIENT', str(pathlib.Path(__file__).resolve().parent /
                                                      'm1n1-work/source/proxyclient')))
from m1n1.setup import *  # noqa: F401,F403

T0 = time.monotonic()
def log(m): print(f'[{time.monotonic() - T0:8.3f}] {m}', flush=True)

buf = u.memalign(0x4000, 0x4000)
def block(lba):
    if not p.main_storage_read(lba, buf):
        raise SystemExit(f'RESULT FAIL: main_storage_read({lba})')
    return iface.readmem(buf, 4096)

log(f'main_storage_init: {p.main_storage_init()}')
try:
    header = block(1)
    if header[:8] != b'EFI PART':
        raise SystemExit(f'RESULT FAIL: LBA 1 is {header[:16].hex()}')
    guid = uuid.UUID(bytes_le=header[56:72])
    entries_lba, count, size = struct.unpack_from('<QII', header, 72)
    log(f'GPT disk GUID {guid}, {count} entries of {size} B at LBA {entries_lba}')
    table = b''.join(block(entries_lba + i) for i in range((count * size + 4095) // 4096))
    for i in range(count):
        e = table[i * size:(i + 1) * size]
        if e[:16] != bytes(16):
            first, last = struct.unpack_from('<QQ', e, 32)
            log(f'  part {i + 1}: {uuid.UUID(bytes_le=e[:16])} {first}-{last} '
                f'"{e[56:128].decode("utf-16-le", "replace").rstrip(chr(0))}"')
    started = time.monotonic()
    for lba in range(16, 16 + 256):
        block(lba)
    log(f'256 single-block reads in {time.monotonic() - started:.2f} s')
    print(f'RESULT PASS {guid}', flush=True)
finally:
    p.main_storage_shutdown()
    log('main_storage_shutdown done')

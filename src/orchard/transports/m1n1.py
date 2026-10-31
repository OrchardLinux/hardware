"""Minimal m1n1 USB proxy client: the operations `orchard launch` needs.

Implements m1n1's UART proxy framing (proxyclient/m1n1/proxy.py upstream)
for memory transfer, heap, main_storage_* and kboot. Op numbers are fixed in
m1n1's src/proxy.h. No m1n1 Python tree or extra dependency is needed.
"""
import fcntl
import os
import select
import struct
import termios
import time

REQ_NOP, REQ_PROXY, REQ_MEMREAD, REQ_MEMWRITE = 0x00AA55FF, 0x01AA55FF, 0x02AA55FF, 0x03AA55FF
REQ_BOOT, REQ_EVENT = 0x04AA55FF, 0x05AA55FF
CMD_LEN, REPLY_LEN, EVENT_HDR_LEN = 56, 36, 8

P_MEMCPY8 = 0x203
P_HEAPBLOCK_ALLOC, P_HEAPBLOCK_SET_LIMIT = 0x600, 0x604
P_KBOOT_BOOT, P_KBOOT_SET_CHOSEN, P_KBOOT_SET_INITRD, P_KBOOT_PREPARE_DT = 0x700, 0x701, 0x702, 0x703
P_SMP_START_SECONDARIES = 0x500
P_GZDEC = 0x401
P_MAIN_STORAGE_INIT, P_MAIN_STORAGE_SHUTDOWN, P_MAIN_STORAGE_READ = 0xf08, 0xf09, 0xf0a
P_CPUFREQ_INIT = 0x1300

M1N1_HEAP = 128 << 20   # room left to m1n1's own heap, as proxyclient does
HOST_HEAP = 1 << 30
BLOCK = 4096
MAX_BLOCKS = 256   # one NVMe command in m1n1's apcie backend (orchard m1n1 patch 0003)


class M1n1Error(RuntimeError):
    pass


def checksum(data):
    value = 0xDEADBEEF
    for byte in data:
        value = (value * 31337 + (byte ^ 0x5a)) & 0xFFFFFFFF
    return value ^ 0xADDEDBAD


class M1n1Proxy:
    def __init__(self, path, timeout=10.0):
        self.path, self.timeout = path, timeout
        self.fd = None
        self.console = bytearray()   # m1n1 log text that arrives between replies

    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            settings = termios.tcgetattr(self.fd)
            settings[0] = settings[1] = settings[3] = 0
            settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            settings[4] = settings[5] = termios.B115200
            settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
            termios.tcflush(self.fd, termios.TCIOFLUSH)
            self._command(REQ_NOP, struct.pack('<Q', 0))   # no optional features: keep data checksums
            self._reply(REQ_NOP)
            self.heap = self.call(P_HEAPBLOCK_ALLOC, 0) + M1N1_HEAP
            self.call(P_HEAPBLOCK_SET_LIMIT, self.heap)
            self.heap_top = self.heap + HOST_HEAP
            return self
        except BaseException:
            self.__exit__()
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    # framing
    def _write(self, data):
        view = memoryview(data)
        while view:
            _, ready, _ = select.select([], [self.fd], [], self.timeout)
            if not ready:
                raise M1n1Error('m1n1 proxy write timed out')
            view = view[os.write(self.fd, view[:65536]):]

    def _read(self, size):
        out = bytearray()
        deadline = time.monotonic() + self.timeout
        while len(out) < size:
            ready, _, _ = select.select([self.fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                raise M1n1Error(f'm1n1 proxy timed out ({len(out)}/{size} bytes)')
            chunk = os.read(self.fd, size - len(out))
            if not chunk:
                raise M1n1Error('m1n1 proxy port closed')
            out += chunk
        return bytes(out)

    def _command(self, kind, payload=b''):
        frame = struct.pack('<I', kind) + payload.ljust(CMD_LEN, b'\0')
        self._write(frame + struct.pack('<I', checksum(frame)))

    def _reply(self, kind):
        while True:
            # Resynchronise on the ff 55 aa prefix; anything else is m1n1 console text.
            byte = self._read(1)
            if byte != b'\xff':
                self.console += byte
                continue
            head = byte + self._read(3)
            if head[1:3] != b'\x55\xaa':
                self.console += head
                continue
            command = struct.unpack('<I', head)[0]
            if command == REQ_EVENT:
                rest = self._read(EVENT_HDR_LEN - 4)
                length = struct.unpack('<H', rest[:2])[0]
                self._read(length + 4)
                continue
            body = head + self._read(REPLY_LEN - 4)
            status, data, check = struct.unpack('<i24sI', body[4:])
            if check != checksum(body[:-4]):
                raise M1n1Error('m1n1 reply checksum error')
            if command != kind:
                raise M1n1Error(f'm1n1 reply mismatch: {command:#x} for {kind:#x}')
            if status != 0:
                raise M1n1Error(f'm1n1 request failed with status {status}')
            return data

    def call(self, opcode, *args, signed=False):
        args = [a & ((1 << 64) - 1) for a in args] + [0] * (6 - len(args))
        self._command(REQ_PROXY, struct.pack('<7Q', opcode, *args))
        reply = self._reply(REQ_PROXY)
        op, status, value = struct.unpack('<Qq' + ('q' if signed else 'Q'), reply)
        if op != opcode or status != 0:
            raise M1n1Error(f'm1n1 op {opcode:#x} failed (status {status})')
        return value

    def write(self, addr, data):
        self._command(REQ_MEMWRITE, struct.pack('<QQI', addr, len(data), checksum(data)))
        self._write(data)
        self._reply(REQ_MEMWRITE)

    def read(self, addr, size):
        self._command(REQ_MEMREAD, struct.pack('<QQ', addr, size))
        expected = struct.unpack('<I', self._reply(REQ_MEMREAD)[:4])[0]
        data = self._read(size)
        if checksum(data) != expected:
            raise M1n1Error('m1n1 memory read checksum error')
        return data

    # host-managed heap above m1n1's own, as proxyclient does
    def memalign(self, align, size):
        addr = (self.heap + align - 1) & ~(align - 1)
        if addr + size > self.heap_top:
            raise M1n1Error('m1n1 host heap exhausted')
        self.heap = addr + size
        return addr

    # storage
    def storage_init(self):
        if not self.call(P_MAIN_STORAGE_INIT):
            raise M1n1Error('This loader has no storage support for the device '
                            '(m1n1 main_storage_init failed)')
        self.staging = self.memalign(BLOCK, BLOCK)

    def storage_shutdown(self):
        self.call(P_MAIN_STORAGE_SHUTDOWN)

    def read_into(self, lba, addr, count=1):
        """count blocks from lba to device memory; the count needs orchard m1n1 patch 0003."""
        if not self.call(P_MAIN_STORAGE_READ, lba, addr, count if count > 1 else 0):
            raise M1n1Error(f'm1n1 storage read of {count} block(s) at {lba} failed')

    def read_block(self, lba):
        self.read_into(lba, self.staging)
        return self.read(self.staging, BLOCK)

    def load_runs(self, runs, addr):
        """Copy disk byte runs to device memory at addr without crossing USB."""
        offset = 0
        for start, length in runs:
            while length:
                lba, skip = divmod(start, BLOCK)
                whole = min(length // BLOCK, MAX_BLOCKS)
                if skip == 0 and whole and (addr + offset) % BLOCK == 0:
                    self.read_into(lba, addr + offset, whole)
                    count = whole * BLOCK
                else:
                    count = min(BLOCK - skip, length)
                    self.read_into(lba, self.staging)
                    self.call(P_MEMCPY8, addr + offset, self.staging + skip, count)
                start, length, offset = start + count, length - count, offset + count
        return offset

    # kboot
    def string(self, text):
        data = text.encode() + b'\0'
        addr = self.memalign(8, len(data))
        self.write(addr, data)
        return addr

    def gzdec(self, src, size, dst, dst_size):
        result = self.call(P_GZDEC, src, size, dst, dst_size, signed=True)
        if result < 0:
            raise M1n1Error('Kernel decompression failed')
        return result

    def boot_linux(self, kernel, dtb, initrd, initrd_size, bootargs):
        self.call(P_KBOOT_SET_CHOSEN, self.string('bootargs'), self.string(bootargs))
        if initrd_size:
            self.call(P_KBOOT_SET_INITRD, initrd, initrd_size)
        self.call(P_CPUFREQ_INIT)
        self.call(P_SMP_START_SECONDARIES)
        if self.call(P_KBOOT_PREPARE_DT, dtb):
            raise M1n1Error('m1n1 could not prepare the device tree; console: ' +
                            self.console[-2000:].decode('utf-8', 'replace'))
        # On success m1n1 leaves the proxy and starts the kernel; a reply may not follow.
        self._command(REQ_PROXY, struct.pack('<7Q', P_KBOOT_BOOT, kernel, 0, 0, 0, 0, 0))
        try:
            self._reply(REQ_PROXY)
        except M1n1Error:
            pass

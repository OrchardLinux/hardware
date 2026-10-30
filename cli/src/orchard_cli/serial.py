"""Bounded USB-serial command and binary transfer primitives."""
import fcntl
import os
import re
import select
import shlex
import stat
import subprocess
import sys
import termios
import time
import secrets


class SerialError(RuntimeError):
    pass


class SerialPort:
    def __init__(self, path, check, prompt=rb'(?:^|[\r\n])/ # '):
        self.path, self.check, self.prompt = path, check, re.compile(prompt)
        self.fd = self.previous = None
        self.pending = bytearray()

    def __enter__(self):
        valid = (self.path and
                 ((sys.platform == 'darwin' and self.path.startswith('/dev/cu.')) or
                  (sys.platform == 'linux' and re.fullmatch(r'/dev/ttyACM[0-9]+', self.path))))
        if not valid:
            raise SerialError('Recovery USB serial port unavailable')
        if sys.platform == 'darwin':
            result = subprocess.run(['/usr/sbin/lsof', '-t', self.path,
                                     self.path.replace('/cu.', '/tty.')],
                                    capture_output=True, timeout=5)
            if result.returncode != 1 or result.stdout.strip():
                raise SerialError('Recovery USB serial port is in use')
        self.check()
        try:
            self.fd = os.open(self.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.ioctl(self.fd, termios.TIOCEXCL if sys.platform == 'darwin' else 0x540C)
            if not stat.S_ISCHR(os.fstat(self.fd).st_mode):
                raise SerialError('Recovery serial port is not a character device')
            self.check()
            self.previous = termios.tcgetattr(self.fd)
            settings = termios.tcgetattr(self.fd)
            settings[0] = settings[1] = settings[3] = 0
            settings[2] = termios.CS8 | termios.CREAD | termios.CLOCAL
            settings[4] = settings[5] = termios.B115200
            settings[6][termios.VMIN] = settings[6][termios.VTIME] = 0
            termios.tcsetattr(self.fd, termios.TCSANOW, settings)
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def __exit__(self, *_):
        if self.fd is not None:
            try:
                if self.previous is not None:
                    termios.tcsetattr(self.fd, termios.TCSANOW, self.previous)
            except (OSError, termios.error):
                pass
            os.close(self.fd)
            self.fd = None

    def write(self, data, deadline):
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [self.fd], [], remaining)[1]:
                raise SerialError('Serial write timed out; command outcome unknown')
            try:
                sent = os.write(self.fd, data)
            except BlockingIOError:
                continue
            if sent <= 0:
                raise SerialError('Serial disconnected; command outcome unknown')
            data = data[sent:]

    def _receive(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
            raise SerialError('Serial response timed out; command outcome unknown')
        try:
            block = os.read(self.fd, 4096)
        except BlockingIOError:
            return
        if not block:
            raise SerialError('Serial disconnected; command outcome unknown')
        self.pending.extend(block)

    def until(self, pattern, deadline, limit=65536):
        while True:
            snapshot = bytes(self.pending)
            match = pattern.search(snapshot)
            if match:
                data = snapshot[:match.end()]
                del self.pending[:match.end()]
                return data, match
            if len(self.pending) > limit:
                raise SerialError('Unexpectedly large serial response; outcome unknown')
            self._receive(deadline)

    def exact(self, size, deadline):
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise SerialError('Invalid binary transfer length')
        output = bytearray()
        while len(output) < size:
            if not self.pending:
                self._receive(deadline)
            count = min(size - len(output), len(self.pending))
            output.extend(self.pending[:count])
            del self.pending[:count]
        return bytes(output)

    def run(self, command, timeout=25):
        deadline = time.monotonic() + timeout
        self.write(b'\r', deadline)
        self.until(self.prompt, deadline)
        token = 'OMA_' + secrets.token_hex(12)
        line = ("printf '\\n%s\\n' " + shlex.quote(token + '_BEGIN') + '; sh -c ' +
                shlex.quote(command) + '; code=$?; printf \'\\n%s:%s\\n\' ' +
                shlex.quote(token + '_DONE') + ' "$code"\r')
        self.write(line.encode(), deadline)
        raw, finish = self.until(re.compile(rb'(?:\r?\n)' + token.encode() +
                                            rb'_DONE:([0-9]{1,3})\r?\n'), deadline)
        begin = re.search(rb'(?:\r?\n)' + token.encode() + rb'_BEGIN\r?\n', raw)
        if not begin or begin.end() > finish.start():
            raise SerialError('Serial markers missing; command outcome unknown')
        return int(finish.group(1)), raw[begin.end():finish.start()].decode('utf-8', errors='replace')

    def capture_exact(self, command, size, timeout=60):
        """Run a read-only producer and receive exactly size raw bytes."""
        if not 0 < size <= 16 * 1024 * 1024:
            raise SerialError('Binary transfer length outside bounds')
        deadline = time.monotonic() + timeout
        self.write(b'\r', deadline)
        self.until(self.prompt, deadline)
        token = 'OMA_' + secrets.token_hex(12)
        # Remote tty output processing would replace binary LF with CRLF.
        line = ("stty -echo -opost; printf '\\n%s\\n' " + shlex.quote(token + '_BEGIN') +
                '; ' + command + "; code=$?; printf '\\n%s:%s\\n' " +
                shlex.quote(token + '_DONE') + ' "$code"; stty echo opost\r')
        self.write(line.encode(), deadline)
        self.until(re.compile(rb'(?:\r?\n)' + token.encode() + rb'_BEGIN\n'), deadline)
        data = self.exact(size, deadline)
        tail, match = self.until(re.compile(rb'\n' + token.encode() +
                                            rb'_DONE:([0-9]{1,3})\n'), deadline, limit=4096)
        if match.start() != 0 or int(match.group(1)) != 0:
            raise SerialError('Binary producer failed or returned extra bytes; outcome unknown')
        return data

    def send_exact(self, command, data, timeout=60):
        """Run a consumer reading exactly len(data) raw bytes from the shell's tty.

        The length goes into the command (e.g. head -c N), never a heredoc; the
        caller checks the result's checksum.
        """
        if not 0 < len(data) <= 16 * 1024 * 1024:
            raise SerialError('Binary transfer length outside bounds')
        deadline = time.monotonic() + timeout
        self.write(b'\r', deadline)
        self.until(self.prompt, deadline)
        token = 'OMA_' + secrets.token_hex(12)
        # Raw mode: no echo, no flow-control or line-editing bytes taken out.
        line = ("stty raw -echo; printf '\\n%s\\n' " + shlex.quote(token + '_BEGIN') +
                '; ' + command + "; code=$?; stty sane; printf '\\n%s:%s\\n' " +
                shlex.quote(token + '_DONE') + ' "$code"\r')
        self.write(line.encode(), deadline)
        self.until(re.compile(rb'(?:\r?\n)' + token.encode() + rb'_BEGIN\r?\n'), deadline)
        self.write(bytes(data), deadline)
        _, match = self.until(re.compile(rb'\r?\n' + token.encode() +
                                         rb'_DONE:([0-9]{1,3})\r?\n'), deadline)
        if int(match.group(1)) != 0:
            raise SerialError('Binary consumer failed; outcome unknown')

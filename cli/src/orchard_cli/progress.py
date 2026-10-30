"""Terminal progress, private diagnostics and non-overlapping installation timing."""
from contextlib import contextmanager
import os
import sys
import tempfile
import threading
import time


class Clock:
    def __init__(self):
        self.started = time.monotonic()
        self.excluded = {'download': 0.0, 'input': 0.0}
        self.phases = []
        self.lock = threading.Lock()
        self.active = {}

    @contextmanager
    def exclude(self, kind):
        with self.lock:
            count, began = self.active.get(kind, (0, time.monotonic()))
            self.active[kind] = (count + 1, began)
        try:
            yield
        finally:
            with self.lock:
                count, began = self.active[kind]
                if count == 1:
                    self.excluded[kind] += time.monotonic() - began
                    del self.active[kind]
                else:
                    self.active[kind] = (count - 1, began)

    def result(self):
        elapsed = time.monotonic() - self.started
        return {'install_seconds': max(0, elapsed - sum(self.excluded.values())),
                'elapsed_seconds': elapsed, 'excluded_seconds': dict(self.excluded)}


class Progress:
    def __init__(self, output=None, *, verbose=False):
        self.output = output or sys.stderr
        self.lock = threading.RLock()
        self.started = time.monotonic()
        self.last = None
        self.initial = None
        self.interactive = bool(getattr(self.output, 'isatty', lambda: False)())
        self.active_line = False
        self.verbose = verbose
        self.rows = {}
        self.rendered = 0
        self.log_path = None

    def task(self, name):
        return TaskProgress(self, name)

    def end_line(self):
        if self.active_line:
            print(file=self.output, flush=True)
            self.active_line = False
        self.rows.clear()
        self.rendered = 0

    def phase(self, message):
        with self.lock:
            self.end_line()
            print(message, file=self.output, flush=True)

    def diagnostic(self, message):
        with self.lock:
            if self.log_path is None:
                from .cache import cache_root
                directory = cache_root() / 'private/diagnostics'
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd, self.log_path = tempfile.mkstemp(prefix='session-', suffix='.log', dir=directory)
                os.close(fd)
            with open(self.log_path, 'a') as stream:
                stream.write(str(message) + '\n')
            if self.verbose:
                self.phase(str(message))

    def transfer(self, sent, total):
        now = time.monotonic()
        with self.lock:
            if self.initial is None:
                self.initial = sent
            interval = 0.2 if self.interactive else 10
            if sent != total and self.last is not None and now - self.last < interval:
                return
            self.last = now
            message = transfer_text(sent, total, self.started, self.initial, now)
            if self.interactive:
                print('\r\033[2K' + message, end='', file=self.output, flush=True)
                self.active_line = True
                if sent == total:
                    self.end_line()
            else:
                print(message, file=self.output, flush=True)

    def reset_transfer(self):
        with self.lock:
            self.end_line()
            self.started, self.last, self.initial = time.monotonic(), None, None

    def event(self, record):
        # Internal hardware events are diagnostics, not user-facing phases.
        self.diagnostic(str(record))

    def row(self, name, message, *, complete=False):
        with self.lock:
            self.rows[name] = message
            if self.interactive:
                if self.rendered:
                    self.output.write(f'\033[{self.rendered}A')
                for label, text in self.rows.items():
                    self.output.write(f'\r\033[2K  {label:<20.20} {text}\n')
                self.output.flush()
                self.rendered = len(self.rows)
            elif complete:
                print(f'  {name:<20.20} {message}', file=self.output, flush=True)


def transfer_text(sent, total, started, initial, now):
    rate = max(0, sent - initial) / max(now - started, 0.001)
    if total is None:  # size known only once done, as for byte-range reads
        return f'{sent / 1048576:5.0f} MiB  {rate / 1048576:5.1f} MiB/s'
    unit, divisor = ('KiB', 1024) if total < 1048576 else ('MiB', 1048576)
    eta = f'{(total - sent) / rate:.0f}s' if rate and sent < total else '—'
    return (f'{sent * 100 / max(total, 1):3.0f}%  '
            f'{sent / divisor:5.0f} / {total / divisor:5.0f} {unit}  '
            f'{rate / 1048576:5.1f} MiB/s  {eta:>5}')


class TaskProgress(Progress):
    """A named row sharing the parent's terminal and output lock."""
    def __init__(self, parent, name):
        super().__init__(parent.output, verbose=parent.verbose)
        self.parent, self.name = parent, name
        self.started, self.last, self.initial = time.monotonic(), None, None

    def task(self, name):
        return self.parent.task(name)

    def phase(self, message):
        self.parent.row(self.name, message, complete=True)

    def reset_transfer(self):
        self.started, self.last, self.initial = time.monotonic(), None, None

    def transfer(self, sent, total):
        now = time.monotonic()
        if self.initial is None:
            self.initial = sent
        interval = 0.2 if self.parent.interactive else 10
        if sent != total and self.last is not None and now - self.last < interval:
            return
        self.last = now
        self.parent.row(self.name, transfer_text(sent, total, self.started, self.initial, now),
                        complete=sent == total or not self.parent.interactive)

    def diagnostic(self, message):
        self.parent.diagnostic(message)

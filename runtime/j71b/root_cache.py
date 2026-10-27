"""Public compressed root cache on the selected ext4 filesystem, never in /omarchy.

Every operation is relative to no-follow directory descriptors. A filename is
only a lookup key: reuse always hashes the complete, exact-sized regular file.
Callers own the device operation lock and the existing storage fencing/mounts.
"""
from contextlib import contextmanager
import hashlib
import os
import re
import stat
import uuid

DIRECTORY = '.orchard-root-cache'
ENTRY = re.compile(r'[0-9a-f]{64}-[1-9][0-9]*\.archive\Z')
PENDING = re.compile(r'\.pending-[0-9a-f]{32}\Z')
MAX_ENTRIES = 16  # Bound discovery even if an interrupted writer left debris.


def key(image):
    if (not isinstance(image.get('sha256'), str) or
            not re.fullmatch(r'[0-9a-f]{64}', image['sha256']) or
            type(image.get('bytes')) is not int or image['bytes'] <= 0):
        raise ValueError('Invalid trusted root cache receipt')
    return '%s-%d.archive' % (image['sha256'], image['bytes'])


def allocation(image, block_size):
    """Additional reservation for a new archive and directory metadata."""
    key(image)
    return ((image['bytes'] + block_size - 1) // block_size + 2) * block_size


class Cache:
    def __init__(self, root, *, create=False, owner_uid=0):
        self.fd = None
        self.owner_uid = owner_uid
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self.device = os.fstat(root_fd).st_dev
            if create:
                try:
                    os.mkdir(DIRECTORY, 0o700, dir_fd=root_fd)
                    os.fsync(root_fd)
                except FileExistsError:
                    pass
            try:
                self.fd = os.open(DIRECTORY, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                  dir_fd=root_fd)
            except FileNotFoundError:
                return
            info = os.fstat(self.fd)
            if (info.st_uid != owner_uid or info.st_dev != self.device or
                    stat.S_IMODE(info.st_mode) != 0o700):
                raise ValueError('Unsafe root cache directory')
            self.entries()  # Unknown, linked, or unprotected entries fail closed.
        except BaseException:
            self.close()
            raise
        finally:
            os.close(root_fd)

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def check_file(self, info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner_uid or
                stat.S_IMODE(info.st_mode) != 0o600 or info.st_dev != self.device or
                info.st_nlink != 1):
            raise ValueError('Unsafe root cache entry')

    def entries(self):
        if self.fd is None:
            return {}
        entries = {}
        # scandir rather than listdir keeps attacker-created huge directories bounded.
        with os.scandir(self.fd) as listing:
            for entry in listing:
                if len(entries) >= MAX_ENTRIES:
                    raise ValueError('Too many root cache entries')
                if not (ENTRY.fullmatch(entry.name) or PENDING.fullmatch(entry.name)):
                    raise ValueError('Unknown root cache entry')
                info = entry.stat(follow_symlinks=False)
                self.check_file(info)
                entries[entry.name] = info
        return entries

    @contextmanager
    def verified(self, image):
        """Yield the fully checked open archive, or None for absence/corruption."""
        name = key(image)
        stream = None
        try:
            if self.fd is not None:
                try:
                    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
                except FileNotFoundError:
                    fd = None
                if fd is not None:
                    stream = os.fdopen(fd, 'rb')
                    before = os.fstat(stream.fileno())
                    self.check_file(before)
                    if before.st_size == image['bytes']:
                        digest = hashlib.sha256()
                        remaining = image['bytes']
                        while remaining:
                            data = stream.read(min(1048576, remaining))
                            if not data:
                                break
                            remaining -= len(data)
                            digest.update(data)
                        after = os.fstat(stream.fileno())
                        if (not remaining and not stream.read(1) and
                                digest.hexdigest() == image['sha256'] and
                                (before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                                (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                            stream.seek(0)
                            yield stream
                            return
            yield None
        finally:
            if stream is not None:
                stream.close()

    def pending_usage(self):
        entries = [info for name, info in self.entries().items() if PENDING.fullmatch(name)]
        return sum(info.st_blocks * 512 for info in entries), len(entries)

    def cleanup_pending(self):
        entries = self.entries()  # Validate the whole namespace before unlinking anything.
        for name in entries:
            if PENDING.fullmatch(name):
                os.unlink(name, dir_fd=self.fd)
        if self.fd is not None:
            os.fsync(self.fd)

    def retain(self, name=None):
        """Collapse a rename-before-prune power loss back to one completed file."""
        entries = self.entries()
        completed = {entry: info for entry, info in entries.items() if ENTRY.fullmatch(entry)}
        if name is None and completed:
            name = max(completed, key=lambda entry: (completed[entry].st_mtime_ns, entry))
        for entry in completed:
            if entry != name:
                os.unlink(entry, dir_fd=self.fd)
        if self.fd is not None:
            os.fsync(self.fd)

    @contextmanager
    def store(self, source, image):
        """Tee only incoming public bytes; commit explicitly after root validation."""
        key(image)
        if self.fd is None:
            raise ValueError('Root cache directory is absent')
        self.cleanup_pending()
        self.retain()
        name = '.pending-' + uuid.uuid4().hex
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=self.fd)
        writer = Writer(source, os.fdopen(fd, 'wb'), image)
        try:
            yield writer
            if writer.committed:
                entries = self.entries()
                destination = key(image)
                os.replace(name, destination, src_dir_fd=self.fd, dst_dir_fd=self.fd)
                os.fsync(self.fd)
                # Retain just the latest validated image. Never recurse or touch /omarchy.
                for old in entries:
                    if old != name and old != destination:
                        os.unlink(old, dir_fd=self.fd)
                os.fsync(self.fd)
        finally:
            try:
                writer.output.close()
            finally:
                # A final buffered flush can fail (ENOSPC/EIO). It must not
                # skip removing an incomplete stream after a caught failure.
                try:
                    os.unlink(name, dir_fd=self.fd)
                    os.fsync(self.fd)
                except FileNotFoundError:
                    pass


class Writer:
    def __init__(self, source, output, image):
        key(image)
        self.source, self.output, self.image = source, output, image
        self.count = 0
        self.digest = hashlib.sha256()
        self.committed = False

    def read(self, size=-1):
        data = self.source.read(min(size if size >= 0 else 1048576, 1048576))
        self.count += len(data)
        if self.count > self.image['bytes']:
            raise ValueError('Incoming cache stream exceeds declared size')
        self.digest.update(data)
        self.output.write(data)
        return data

    def commit(self):
        if self.count != self.image['bytes'] or self.digest.hexdigest() != self.image['sha256']:
            raise ValueError('Incoming cache integrity mismatch')
        self.output.flush()
        os.fsync(self.output.fileno())
        self.committed = True

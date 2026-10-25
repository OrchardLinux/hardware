"""Constrained streaming tar extraction. No archive member may traverse a link.

Links are materialized only after every regular file has been written using
no-follow directory descriptors. Absolute symlink targets are allowed for a
Linux root, but are never followed by this extractor. Devices/FIFOs are refused.
"""
import gzip
import hashlib
import os
from pathlib import PurePosixPath
import tarfile


class CountingReader:
    def __init__(self, source, limit):
        self.source, self.limit, self.count = source, limit, 0
        self.digest = hashlib.sha256()

    def read(self, size=-1):
        data = self.source.read(min(size if size >= 0 else 1048576, 1048576))
        self.count += len(data)
        if self.count > self.limit:
            raise ValueError('Incoming stream exceeds declared size')
        self.digest.update(data)
        return data


def safe_name(name):
    if not isinstance(name, str) or '\x00' in name or name.startswith('/'):
        raise ValueError('Absolute or invalid archive path')
    parts = PurePosixPath(name).parts
    if not parts or '..' in parts:
        raise ValueError('Archive path escapes staging')
    return '/'.join(parts)


def parent_fd(root, name):
    parts = safe_name(name).split('/')
    current = os.dup(root)
    try:
        for part in parts[:-1]:
            created = False
            try:
                os.mkdir(part, 0o755, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = following
            if created:
                # mkdir's mode is masked by the SSH daemon's umask. Only
                # normalize new implicit parents; explicit archive directory
                # metadata is applied separately after extraction.
                os.fchmod(current, 0o755)
        return current, parts[-1]
    except BaseException:
        os.close(current)
        raise


def extract(source, destination, *, size, sha256, expanded_limit, ownership=True, compression='gzip'):
    incoming = CountingReader(source, size)
    root = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    seen, links, directories = set(), [], []
    expanded = 0
    try:
        if compression == 'zstd':
            from compression.zstd import ZstdFile
            decoder = ZstdFile(incoming, mode='rb')
        elif compression == 'gzip':
            decoder = gzip.GzipFile(fileobj=incoming, mode='rb')
        else:
            raise ValueError('Unsupported root compression')
        with decoder as compressed:
            with tarfile.open(fileobj=compressed, mode='r|') as archive:
                for member in archive:
                    if member.name in ('.', './') and member.isdir():
                        continue
                    name = safe_name(member.name)
                    if name.split('/')[0].startswith('.orchard-'):
                        raise ValueError('Archive uses reserved installation metadata')
                    if name in seen:
                        raise ValueError('Duplicate archive member: ' + name)
                    seen.add(name)
                    if member.issym() or member.islnk():
                        if member.islnk():
                            safe_name(member.linkname)
                        elif '\x00' in member.linkname or not member.linkname:
                            raise ValueError('Invalid symlink')
                        links.append((name, member))
                        continue
                    if not (member.isdir() or member.isfile()):
                        raise ValueError('Special archive member refused: ' + name)
                    parent, leaf = parent_fd(root, name)
                    try:
                        if member.isdir():
                            try:
                                os.mkdir(leaf, 0o755, dir_fd=parent)
                            except FileExistsError:
                                fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                                os.close(fd)
                            directories.append((name, member))
                        else:
                            expanded += member.size
                            if expanded > expanded_limit:
                                raise ValueError('Expanded root exceeds declared limit')
                            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                         0o600, dir_fd=parent)
                            with os.fdopen(fd, 'wb') as output:
                                content = archive.extractfile(member)
                                remaining = member.size
                                while remaining:
                                    block = content.read(min(1048576, remaining))
                                    if not block:
                                        raise ValueError('Truncated archive member')
                                    output.write(block)
                                    remaining -= len(block)
                                output.flush()
                                if ownership:
                                    os.fchown(output.fileno(), member.uid, member.gid)
                                os.utime(output.fileno(), (member.mtime, member.mtime))
                                os.fchmod(output.fileno(), member.mode & 0o7777)
                    finally:
                        os.close(parent)
            # Verify gzip CRC/footer and hash ALL incoming bytes, not just the
            # prefix tarfile consumed before its end-of-archive records.
            while compressed.read(1048576):
                pass
        while incoming.read(1048576):
            pass
        if incoming.count != size or incoming.digest.hexdigest() != sha256:
            raise ValueError('Incoming archive integrity mismatch')
        # Hardlinks first, only to regular files. Neither side follows symlinks.
        for name, member in sorted(links, key=lambda item: item[1].issym()):
            parent, leaf = parent_fd(root, name)
            try:
                if member.islnk():
                    src_parent, src_leaf = parent_fd(root, member.linkname)
                    try:
                        fd = os.open(src_leaf, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=src_parent)
                        import stat
                        if not stat.S_ISREG(os.fstat(fd).st_mode):
                            raise ValueError('Hardlink target is not a regular file')
                        os.close(fd)
                        os.link(src_leaf, leaf, src_dir_fd=src_parent, dst_dir_fd=parent, follow_symlinks=False)
                    finally:
                        os.close(src_parent)
                else:
                    os.symlink(member.linkname, leaf, dir_fd=parent)
                    if ownership:
                        os.chown(leaf, member.uid, member.gid, dir_fd=parent, follow_symlinks=False)
            finally:
                os.close(parent)
        for name, member in reversed(directories):
            parent, leaf = parent_fd(root, name)
            try:
                fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                try:
                    if ownership:
                        os.fchown(fd, member.uid, member.gid)
                    os.utime(fd, (member.mtime, member.mtime))
                    os.fchmod(fd, member.mode & 0o7777)
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
        return {'bytes': incoming.count, 'expanded_bytes': expanded, 'members': len(seen)}
    finally:
        os.close(root)

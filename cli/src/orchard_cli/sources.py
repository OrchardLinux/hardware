"""Shared upstream Git objects, with independent editable source checkouts.

Only acquisition and Git objects are shared. Never resets an existing checkout.
Cached revisions are retained by refs; do not prune this cache while checkouts
borrow its objects. Managed by Orchard CLI, independently of distro or hardware repositories.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def cache_root():
    override = os.environ.get('ORCHARD_CACHE_DIR')
    if override:
        return Path(override).expanduser().resolve()
    return (Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'orchard').resolve()


def git(repository, *arguments, env=None):
    return subprocess.check_output(['git', '-C', str(repository), *map(str, arguments)],
                                   env=env, stderr=subprocess.PIPE).strip()


@contextmanager
def repository(url, commit, *, cache=None, donor=None):
    """Yield a bare object store containing commit, fetching only when absent."""
    root = Path(cache) if cache is not None else cache_root()
    root = root / 'git'
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(url.encode()).hexdigest()
    bare = root / (name + '.git')
    with (root / (name + '.lock')).open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if not bare.exists():
            subprocess.run(['git', 'init', '--bare', '-q', str(bare)], check=True)
        try:
            git(bare, 'cat-file', '-e', commit + '^{commit}')
        except subprocess.CalledProcessError:
            origin = url
            if donor is not None and (Path(donor) / '.git').exists():
                try:
                    git(donor, 'cat-file', '-e', commit + '^{commit}')
                    origin = Path(donor).resolve().as_uri()
                except subprocess.CalledProcessError:
                    pass
            print(f'Caching upstream Git revision {commit} from {origin}', flush=True)
            subprocess.run(['git', '-C', str(bare), 'fetch', '--progress', '--no-tags',
                            '--depth', '1', origin, commit], check=True)
        git(bare, 'update-ref', 'refs/heads/orchard/' + commit, commit)
        yield bare


def checkout(url, commit, source, *, cache=None):
    source = Path(source).resolve()
    if source.exists():
        raise ValueError(f'Source directory exists; will not reset it: {source}')
    source.parent.mkdir(parents=True, exist_ok=True)
    with repository(url, commit, cache=cache) as bare:
        with tempfile.TemporaryDirectory(prefix='source-', dir=source.parent) as temporary:
            stage = Path(temporary) / 'source'
            subprocess.run(['git', 'init', '-q', str(stage)], check=True)
            # clone --shared does not reliably borrow objects from shallow repos.
            # Explicit alternates retain one shared upstream object store.
            info = stage / '.git/objects/info'
            info.mkdir(parents=True, exist_ok=True)
            (info / 'alternates').write_text(str(bare / 'objects') + '\n')
            if (bare / 'shallow').exists():
                shutil.copyfile(bare / 'shallow', stage / '.git/shallow')
            git(stage, 'remote', 'add', 'origin', url)
            git(stage, 'checkout', '-q', '--detach', commit)
            stage.rename(source)


def declared_tree(url, commit, patches, *, cache=None, donor=None):
    """Tree produced by the declared patches, without checking out Linux."""
    with repository(url, commit, cache=cache, donor=donor) as bare:
        with tempfile.TemporaryDirectory(prefix='orchard-index-') as temporary:
            env = {**os.environ, 'GIT_INDEX_FILE': str(Path(temporary) / 'index')}
            git(bare, 'read-tree', commit, env=env)
            # One apply for the whole series writes Linux's large index once,
            # not once per patch (13 s for 358 patches).
            if patches:
                git(bare, 'apply', '--cached', '--whitespace=nowarn',
                    *(Path(patch).resolve() for patch in patches), env=env)
            tree = git(bare, 'write-tree', env=env).decode()
            # Keep patch-generated objects reachable as well as upstream commits.
            git(bare, 'update-ref', 'refs/orchard/trees/' + tree, tree)
            return tree


def working_tree(source):
    """Snapshot tracked/untracked source edits without changing the user's index."""
    source = Path(source).resolve()
    with tempfile.TemporaryDirectory(prefix='orchard-index-') as temporary:
        index = Path(temporary) / 'index'
        original = Path(os.fsdecode(git(source, 'rev-parse', '--git-path', 'index')))
        if not original.is_absolute():
            original = source / original
        env = {**os.environ, 'GIT_INDEX_FILE': str(index)}
        if original.exists():
            shutil.copyfile(original, index)
        else:
            git(source, 'read-tree', 'HEAD', env=env)
        git(source, 'add', '--all', '--', '.', env=env)
        return git(source, 'write-tree', env=env).decode()



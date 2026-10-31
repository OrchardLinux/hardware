"""Inspect and prune the shared build cache: components-v2 and the recipes' work directories."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil

from .build import INPUTS_RECORD, cache_root, component_lock, remove_unshared, share_files


def _size(path):
    total = 0
    for directory, _, names in os.walk(path):
        for name in names:
            file = Path(directory) / name
            if not file.is_symlink():
                total += file.stat().st_size
    return total


def _on_disk(path):
    """Bytes under PATH, counting hard-linked files once."""
    seen, total = set(), 0
    for directory, _, names in os.walk(path):
        for name in names:
            info = (Path(directory) / name).lstat()
            if (info.st_dev, info.st_ino) not in seen:
                seen.add((info.st_dev, info.st_ino))
                total += info.st_size
    return total


def _human(size):
    for unit in ('B', 'K', 'M', 'G'):
        if size < 1024 or unit == 'G':
            return f'{size:.0f}{unit}' if unit == 'B' else f'{size:.1f}{unit}'
        size /= 1024


def _keyed(directory):
    """(name, key, path) for DIRECTORY/NAME/KEY, oldest use first within each name."""
    if not directory.is_dir():
        return []
    return [(parent.name, entry.name, entry)
            for parent in sorted(directory.iterdir()) if parent.is_dir() and not parent.name.startswith('.')
            for entry in sorted(parent.iterdir(), key=lambda p: p.stat().st_mtime) if entry.is_dir()]


def entries(cache=None):
    """(component, key, path) for published entries and leftover build directories."""
    return _keyed(Path(cache or cache_root()) / 'components-v2')


def work_entries(cache=None):
    """(recipe, key, path) for the recipes' work directories (m1n1, Pongo, recovery tools)."""
    return _keyed(Path(cache or cache_root()) / 'work')


def _summary(path):
    record = path / INPUTS_RECORD
    if not record.is_file():
        return 'inputs not recorded'
    inputs = json.loads(record.read_text())
    if 'tree' in inputs:
        return f"tree {inputs['tree'][:12]}"
    if 'base' in inputs:
        return f"base {inputs['base'][:12]} dtb {inputs.get('dtb')}"
    return ''


def show(cache=None):
    root = Path(cache or cache_root())
    rows = entries(root)
    total = 0
    for component, key, path in rows:
        size = _size(path)
        total += size
        when = datetime.fromtimestamp(path.stat().st_mtime).strftime('%Y-%m-%d %H:%M')  # last use
        state = 'unfinished build' if key.startswith('build-') else _summary(path)
        print(f'{component:22} {key[:12]:12} {_human(size):>7}  {when}  {state}')
    components = root / 'components-v2'
    if rows:
        print(f'{len(rows)} entries, {_human(total)} ({_human(_on_disk(components))} on disk) in {components}')
    else:
        print('Component cache is empty')
    compiler = root / 'ccache'
    if compiler.is_dir():
        # ccache keeps itself within its own size limit (5 GB by default).
        print(f'Compiler cache {_human(_on_disk(compiler))} in {compiler}')
    work = work_entries(root)
    if work:
        recipes = sorted({recipe for recipe, _, _ in work})
        print(f'{len(work)} work director{"y" if len(work) == 1 else "ies"} ({", ".join(recipes)}), '
              f'{_human(_on_disk(root / "work"))} in {root / "work"}')


def remove(selectors, cache=None):
    """Delete entries named COMPONENT/KEY-PREFIX or COMPONENT (all of its entries)."""
    root = Path(cache or cache_root())
    rows = entries(root)
    chosen = []
    for selector in selectors:
        component, _, prefix = selector.partition('/')
        matches = [r for r in rows if r[0] == component and r[1].startswith(prefix)]
        if not matches:
            raise ValueError(f'No cache entry matches {selector}')
        if prefix and len(matches) > 1:
            raise ValueError(f'{selector} is ambiguous; give more of the key')
        chosen += [m for m in matches if m not in chosen]
    # The build lock keeps removal away from a running build and its sources.
    with component_lock(root):
        for component, key, path in chosen:
            shutil.rmtree(path)
            print(f'Removed {component}/{key}')
        remove_unshared(root / 'components-v2')


def prune(keep, cache=None):
    """Keep the KEEP most recently used entries of each component; delete the rest.

    Also removes builds that never finished, shares identical files of
    entries published before sharing existed, and keeps the KEEP most
    recently used work directories of each recipe. Kernel trees are kept:
    they hold editable source.
    """
    if keep < 1:
        raise ValueError('--keep must be at least 1')
    root = Path(cache or cache_root())
    components = root / 'components-v2'
    components.mkdir(parents=True, exist_ok=True)
    with component_lock(root):
        before = _on_disk(components)
        kept = {}
        for component, key, path in reversed(entries(root)):  # newest use first
            if key.startswith('build-') or kept.setdefault(component, 0) >= keep:
                shutil.rmtree(path)
                print(f'Removed {component}/{key}')
                continue
            kept[component] += 1
            share_files(components, path, json.loads((path / 'component.json').read_text()))
        remove_unshared(components)
        work = root / 'work'
        before += _on_disk(work)
        used = {}
        for recipe, key, path in reversed(work_entries(root)):
            used[recipe] = used.get(recipe, 0) + 1
            if used[recipe] > keep:
                shutil.rmtree(path)
                print(f'Removed work {recipe}/{key}')
        after = _on_disk(components) + _on_disk(work)
    print(f'Freed {_human(before - after)}; {_human(after)} on disk in {root}')

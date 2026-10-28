#!/usr/bin/env python3
"""Export a merge-based kernel branch (e.g. Hoolock's bits/*) as ordered patch series.

Replays each first-parent merge's branch commits onto the accumulated stack with a
3-way merge in a bare repository (no checkout: Linux has case-only filename pairs
that macOS filesystems cannot hold). Commits that become empty are dropped. Then
writes one format-patch directory per merged branch and checks that the linear
tip has exactly the original tree.

  linearize-merges.py --repo BARE --base TAG_COMMIT --tip MERGE_COMMIT --output DIR
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def git(repo, *args, check=True, **kwargs):
    result = subprocess.run(['git', '-C', str(repo), *map(str, args)],
                            capture_output=True, text=True, **kwargs)
    if check and result.returncode:
        sys.exit(f'git {args[0]} failed: {result.stdout}{result.stderr}')
    return result.stdout.strip()


def replay(repo, head, commit):
    result = subprocess.run(['git', '-C', str(repo), 'merge-tree', '--write-tree',
                             f'--merge-base={commit}^', head, commit], capture_output=True, text=True)
    if result.returncode:
        sys.exit(f'Conflict replaying {commit}:\n{result.stdout[:4000]}')
    tree = result.stdout.split('\n', 1)[0]
    if tree == git(repo, 'rev-parse', head + '^{tree}'):
        print(f'  drop empty {commit[:12]} {git(repo, "log", "-1", "--format=%s", commit)}')
        return head
    fields = {'GIT_AUTHOR_NAME': 'an', 'GIT_AUTHOR_EMAIL': 'ae', 'GIT_AUTHOR_DATE': 'aI',
              'GIT_COMMITTER_NAME': 'cn', 'GIT_COMMITTER_EMAIL': 'ce', 'GIT_COMMITTER_DATE': 'cI'}
    env = {**os.environ, **{key: git(repo, 'log', '-1', f'--format=%{code}', commit)
                            for key, code in fields.items()}}
    message = git(repo, 'log', '-1', '--format=%B', commit)
    return git(repo, 'commit-tree', tree, '-p', head, input=message + '\n', env=env)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--base', required=True)
    parser.add_argument('--tip', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        shutil.rmtree(args.output)
    head = git(args.repo, 'rev-parse', args.base + '^{commit}')
    merges = git(args.repo, 'rev-list', '--first-parent', '--merges', '--reverse',
                 f'{args.base}..{args.tip}').split()
    for merge in merges:
        subject = git(args.repo, 'log', '-1', '--format=%s', merge)
        name = re.sub(r"[^A-Za-z0-9._-]+", '-', re.sub(r"^Merge branch '(?:bits/)?([^']+)'.*", r'\1', subject))
        start = head
        for commit in git(args.repo, 'rev-list', '--reverse', '--no-merges', f'{merge}^1..{merge}^2').split():
            head = replay(args.repo, head, commit)
        git(args.repo, 'format-patch', '-q', '--zero-commit', '--no-signature',
            '-o', args.output / name, f'{start}..{head}')
        print(f'{name}: {len(list((args.output / name).glob("*.patch")))} patches')
    if git(args.repo, 'rev-parse', head + '^{tree}') != git(args.repo, 'rev-parse', args.tip + '^{tree}'):
        sys.exit('Linearized tree differs from the original tip')
    print(f'Verified: linear tree == {args.tip} tree')


if __name__ == '__main__':
    main()

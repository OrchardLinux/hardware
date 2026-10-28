#!/usr/bin/env python3
"""Replay a contributor's commits onto the stack, stopping at the first conflict.

  replay-commits.py REPO START_REF LISTFILE OUT_REF

LISTFILE holds commit ids in order. Works in a bare repository (3-way via
merge-tree, no checkout). Each replayed commit keeps its author and gains an
Orchard-replay-of trailer, so rerunning resumes after a hand-resolved conflict
(commit the resolution onto OUT_REF with the same trailer, then rerun).
"""
import os, subprocess, sys
repo, start, listfile, outref = sys.argv[1:]
def git(*a, **k):
    return subprocess.run(['git','-C',repo,*a], capture_output=True, text=True, **k)
commits = open(listfile).read().split()
head = git('rev-parse', start).stdout.strip()
done_marker = set(git('log','--format=%(trailers:key=Orchard-replay-of,valueonly)', f'{head}', '-n','400').stdout.split())
for i, c in enumerate(commits):
    full = git('rev-parse', c).stdout.strip()
    if full in done_marker: continue
    r = git('merge-tree','--write-tree',f'--merge-base={c}^',head,c)
    if r.returncode:
        git('update-ref', outref, head)
        print(f'STOP at {i}: {c[:10]} {git("log","-1","--format=%s",c).stdout.strip()}')
        print('conflicted tree', r.stdout.split()[0]); sys.exit(2)
    tree = r.stdout.split('\n',1)[0]
    if tree == git('rev-parse', head+'^{tree}').stdout.strip():
        print('empty', c[:10]); continue
    f = {'GIT_AUTHOR_NAME':'an','GIT_AUTHOR_EMAIL':'ae','GIT_AUTHOR_DATE':'aI','GIT_COMMITTER_NAME':'cn','GIT_COMMITTER_EMAIL':'ce','GIT_COMMITTER_DATE':'cI'}
    env = {**os.environ, **{k: git('log','-1',f'--format=%{v}',c).stdout.strip() for k,v in f.items()}}
    msg = git('log','-1','--format=%B',c).stdout.rstrip('\n') + f'\n\nOrchard-replay-of: {full}\n'
    head = subprocess.run(['git','-C',repo,'commit-tree',tree,'-p',head], input=msg, text=True, capture_output=True, env=env, check=True).stdout.strip()
git('update-ref', outref, head)
print('DONE', head)

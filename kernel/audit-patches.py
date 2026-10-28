#!/usr/bin/env python3
"""Apply the kernel patch series to a scratch copy of pinned source files.

Requires a local upstream git checkout containing upstream.lock's commit.
Does not alter that checkout, download sources, build, or access hardware.
Device-match preservation is a regression check, not a semantic proof.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(ROOT))
from series import patches as series_patches  # noqa: E402


def audit(upstream, device):
    lock = dict(line.split('=', 1) for line in (ROOT / 'upstream.lock').read_text().splitlines())
    patches = series_patches(device)
    originals = {}
    for patch in patches:
        for line in patch.read_text().splitlines():
            if line.startswith('--- a/'):
                path = line[6:]
                if path in originals:
                    continue
                # Files created by an earlier patch in the series are not upstream.
                shown = subprocess.run(['git', '-C', str(upstream), 'show', lock['commit'] + ':' + path],
                                       capture_output=True)
                if shown.returncode == 0:
                    originals[path] = shown.stdout
    with tempfile.TemporaryDirectory(prefix='orchard-patch-audit-') as directory:
        work = Path(directory)
        for path, content in originals.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for patch in patches:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=work, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=work, check=True)
            print('APPLIED', patch.relative_to(ROOT))
        # Compare full files, not just diff removals: replacements may retain IDs.
        match = re.compile(r'\.compatible\s*=\s*"([^"]+)"')
        for path, before in originals.items():
            after = (work / path).read_text()
            lost = set(match.findall(before.decode())) - set(match.findall(after))
            if lost:
                raise RuntimeError(f'{path}: removed device matches: {sorted(lost)}')
        dart = (work / 'drivers/iommu/apple-dart.c').read_text()
        for required in ('args->args_count != 1 && args->args_count != 5',
                         'aperture_start = cfg->dma_min',
                         'aperture_end = cfg->dma_max'):
            if required not in dart:
                raise RuntimeError('DART window compatibility regression: ' + required)
        print(f'PASS: series applies at {lock["commit"]}; existing OF match strings retained in {len(originals)} modified files')
        print('PASS: DART per-master DMA-window contract retained')
        print('LIMIT: no compilation or hardware behavior established by this audit')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upstream', type=Path)
    parser.add_argument('--device', default='j71b')
    args = parser.parse_args()
    audit(args.upstream.resolve(), args.device)

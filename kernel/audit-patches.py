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


def audit(upstream):
    lock = dict(line.split('=', 1) for line in (ROOT / 'upstream.lock').read_text().splitlines())
    patches = sorted((ROOT / 'patches/j71b').glob('*.patch'))
    originals = {}
    for patch in patches:
        for line in patch.read_text().splitlines():
            if line.startswith('--- a/'):
                path = line[6:]
                if path not in originals:
                    originals[path] = subprocess.check_output(
                        ['git', '-C', str(upstream), 'show', lock['commit'] + ':' + path])
    with tempfile.TemporaryDirectory(prefix='orchard-patch-audit-') as directory:
        work = Path(directory)
        for path, content in originals.items():
            target = work / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        for patch in patches:
            subprocess.run(['git', 'apply', '--check', str(patch)], cwd=work, check=True)
            subprocess.run(['git', 'apply', str(patch)], cwd=work, check=True)
            print('APPLIED', patch.name)
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
                         'aperture_end = dma_max',
                         'dma_addr_t dma_max = cfg->dma_max',
                         'dma_min = max(dma_min, dart->dma_min)',
                         'dma_max = min(dma_max, dart->dma_max)'):
            if required not in dart:
                raise RuntimeError('DART window compatibility regression: ' + required)
        print(f'PASS: series applies at {lock["commit"]}; existing OF match strings retained in {len(originals)} modified files')
        print('PASS: DART per-master and controller DMA-window contracts retained')
        print('LIMIT: no compilation or hardware behavior established by this audit')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('upstream', type=Path)
    audit(parser.parse_args().upstream.resolve())

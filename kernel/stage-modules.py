#!/usr/bin/env python3
"""Install built kernel and additional modules into a local filesystem tree."""
import argparse
from pathlib import Path
import shutil
import subprocess
from orchard_hardware import buildenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', type=Path, required=True, help='kernel build output')
    parser.add_argument('--external', type=Path, action='append', default=[], help='additional module build tree')
    parser.add_argument('--output', type=Path, required=True, help='new module staging tree')
    args = parser.parse_args()
    release = (args.build / 'include/config/kernel.release').read_text().strip()
    if not release or '/' in release or args.output.exists():
        parser.error('invalid release or output already exists')
    external = [p for root in args.external for p in root.rglob('*.ko')]
    overrides = {p.stem.replace('-', '_') for p in external}
    builtin = [args.build / n for n in ('modules.builtin', 'modules.builtin.modinfo')]
    if not all(p.is_file() for p in builtin):
        parser.error('missing kernel module metadata')
    rows = []
    for p in sorted(args.build.rglob('*.ko')):
        if p.stem.replace('-', '_') not in overrides:
            rows.append((p, Path('kernel') / p.relative_to(args.build)))
    for p in sorted(external):
        rows.append((p, Path('extra') / p.name))
    names = [str(dest) for _, dest in rows]
    if len(names) != len(set(names)):
        parser.error('duplicate module destination')
    dest = args.output / 'lib/modules' / release
    dest.mkdir(parents=True)
    for source, relative in rows:
        target = dest / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    for p in builtin:
        shutil.copyfile(p, dest / p.name)
    (dest / 'modules.order').write_text(''.join(n + '\n' for n in names))
    if buildenv.native():
        subprocess.run(['depmod', '-b', str(args.output.resolve()), release], check=True)
    else:
        podman = shutil.which('podman')
        if podman is None:
            parser.error('Podman is required')
        subprocess.run([podman, 'run', '--rm', '--network', 'none', '--userns', 'keep-id',
                        '-v', f'{args.output.resolve()}:/stage:rw',
                        'localhost/orchard-hardware-kernel-toolchain:bookworm',
                        'depmod', '-b', '/stage', release], check=True)
    print(f'Staged {len(rows)} modules for {release} in {args.output}')


if __name__ == '__main__':
    main()

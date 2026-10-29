#!/usr/bin/env python3
"""Build the public BusyBox binary used by the J71b RAM runtime."""
import argparse
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('output already exists')
    podman = shutil.which('podman')
    if podman is None:
        parser.error('Podman is required')
    subprocess.run([podman, 'build', '-f', str(HERE / 'Containerfile'),
                    '-t', 'localhost/orchard-hardware-j71b-busybox:bookworm', str(HERE)], check=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('xb') as target:
        subprocess.run([podman, 'run', '--rm', '--network', 'none',
                        'localhost/orchard-hardware-j71b-busybox:bookworm',
                        'cat', '/bin/busybox'], stdout=target, check=True)
    args.output.chmod(0o755)
    print('BusyBox:', args.output)


if __name__ == '__main__':
    main()

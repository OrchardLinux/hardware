#!/usr/bin/env python3
"""Assemble a J71b RAM recovery image from explicit kernel, loader and tool inputs."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / 'boot/initramfs/recovery'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('loader', 'kernel', 'dtb', 'busybox', 'dropbear', 'dropbearkey',
                 'wpa-supplicant', 'wpa-cli', 'iw',
                 'base', 'base-receipt', 'modules-dir', 'root-receipt', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    diagnostics = parser.add_mutually_exclusive_group()
    diagnostics.add_argument('--verbose', action='store_true',
                             help='enable visible Linux framebuffer console and kernel diagnostics')
    args = parser.parse_args()
    manifest = 'boot-verbose.json' if args.verbose else 'boot.json'
    if args.output.exists():
        parser.error('choose a new output path')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    work = args.output.parent / '.work'
    work.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='recovery-stage-', dir=work) as temporary:
        stage = Path(temporary)
        initramfs = stage / 'initramfs.gz'
        subprocess.run([sys.executable, '-m', 'orchard_hardware.image.prepare_runtime',
                        '--busybox', str(args.busybox), '--dropbear', str(args.dropbear),
                        '--dropbearkey', str(args.dropbearkey),
                        '--wpa-supplicant', str(args.wpa_supplicant),
                        '--wpa-cli', str(args.wpa_cli), '--iw', str(args.iw),
                        '--base', str(args.base), '--base-receipt', str(args.base_receipt),
                        '--modules-dir', str(args.modules_dir), '--root-receipt', str(args.root_receipt),
                        '--output', str(stage / 'root')], check=True)
        subprocess.run([sys.executable, '-m', 'orchard_hardware.boot.initramfs.pack',
                        '--root', str(stage / 'root'),
                        '--output', str(initramfs)], check=True)
        subprocess.run([sys.executable, '-m', 'orchard_hardware.boot.assemble',
                        '--manifest', str(HERE / manifest),
                        '--loader', str(args.loader), '--dtb', str(args.dtb),
                        '--kernel', str(args.kernel), '--initramfs', str(initramfs),
                        '--output', str(args.output)], check=True)


if __name__ == '__main__':
    main()

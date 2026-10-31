#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build/test the public C sources using synthetic data; no hardware access."""
from pathlib import Path
import os
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent


def function_span(source, name):
    # Mask comments/strings without changing offsets before counting braces.
    mask = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                  lambda m: ' ' * len(m[0]), source, flags=re.S)
    match = re.search(r'^static\s+[^;{]+?\b' + re.escape(name) + r'\s*\([^;{]*\)\s*\{', mask, re.M)
    if not match:
        raise ValueError(f'function not found: {name}')
    brace = mask.index('{', match.start())
    depth = 1
    end = brace + 1
    while depth:
        depth += (mask[end] == '{') - (mask[end] == '}')
        end += 1
    return match.start(), end


def main():
    cc = os.environ.get('CC', 'cc')
    flags = ['-std=c11', '-Wall', '-Wextra', '-Werror', '-fsanitize=undefined,bounds',
             '-fno-sanitize-recover=all']
    with tempfile.TemporaryDirectory(prefix='j72-public-tests-') as temp:
        build = Path(temp)
        # Compile actual driver implementations into the mocked test harness,
        # so a driver edit cannot silently leave a duplicated test body behind.
        driver = (ROOT / 'battery/bq27xxx_battery_hdq_uart.c').read_text()
        harness = (ROOT / 'battery/test-hdq-core.c').read_text()
        for name in ('hdq_encode_byte', 'hdq_decode_byte', 'hdq_uart_receive_buf', 'hdq_write_byte'):
            a, b = function_span(driver, name)
            x, y = function_span(harness, name)
            harness = harness[:x] + driver[a:b] + harness[y:]
        (build / 'test-hdq.c').write_text(harness)
        cases = [
            ('contacts', ROOT / 'touch/test-contacts.c', ['-I' + str(ROOT / 'touch')]),
            ('hdq', build / 'test-hdq.c', []),
            ('rollback', ROOT / 'charging/test-core.c', []),
        ]
        for name, source, extra in cases:
            binary = build / name
            subprocess.run([cc, *flags, *extra, str(source), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], stdin=subprocess.DEVNULL, check=True)
        subprocess.run(['python3', str(ROOT / 'touch/test-private-includes.py'), '-v'], check=True)
    print('PASS: all public-source offline checks; synthetic inputs only, no device contacted.')


if __name__ == '__main__':
    main()

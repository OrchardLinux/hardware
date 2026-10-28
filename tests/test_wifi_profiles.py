"""Firmware/profile contracts and executable tests of the patched C selection block.

These are host-side regression tests, not proof of radio initialization.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kernel'))
from series import patches as series_patches  # noqa: E402

SELECTION = ROOT / 'kernel/patches/drivers/wifi/0002-wifi-brcmfmac-Select-Apple-NVRAM-by-OTP-when-there-i.patch'
NVRAM = 'brcmfmac4355c1-pcie'


def postimage(path):
    """Retain new-side hunk text (not a complete reconstructed source file)."""
    return '\n'.join(line[1:] for line in path.read_text().splitlines()
                     if line.startswith((' ', '+')) and not line.startswith('+++'))


def block(text, start):
    start = text.index(start)
    opening = text.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (text[end] == '{') - (text[end] == '}')
        end += 1
    return text[start:end]


class WifiProfiles(unittest.TestCase):
    def setUp(self):
        self.recipe = json.loads((ROOT / 'devices/j71b/firmware.json').read_text())
        self.nvram = [row for row in self.recipe['files'] if row['name'].endswith('.txt')]

    def test_all_ipsw_profiles_have_separate_pinned_destinations(self):
        profiles = {'u-4.3': '9b05b7a2ff1b0baea5b8cb8240d1aedbfcdabafa2604742a72b5fe4a15f1b3ca',
                    'u-4.1': '9b05b7a2ff1b0baea5b8cb8240d1aedbfcdabafa2604742a72b5fe4a15f1b3ca',
                    'm-2.5': '0c83b29dd420c77eef163450aef451261dcebc484f6a2f16627f5a4f3cd52f72',
                    'm-2.3': '0c83b29dd420c77eef163450aef451261dcebc484f6a2f16627f5a4f3cd52f72'}
        self.assertEqual(len(self.nvram), len(profiles))
        for profile, digest in profiles.items():
            vendor, version = profile.split('-')
            self.assertIn({'name': f'P-figaroa_M-YSBC_V-{vendor}__m-{version}.txt',
                           'sha256': digest,
                           'destination': f'lib/firmware/brcm/{NVRAM}.apple,j71b-YSBC-{profile}.txt'},
                          self.nvram)

    def test_no_less_specific_nvram_is_packaged(self):
        """Upstream falls back to shorter names; packaging none keeps profiles exact."""
        for row in self.nvram:
            stem = row['destination'].removeprefix(f'lib/firmware/brcm/{NVRAM}.').removesuffix('.txt')
            self.assertRegex(stem, r'^apple,j71b-[A-Za-z0-9]+-[a-z]+-[0-9.]+$')

    def test_selection_c(self):
        """Compile and execute the patched Apple OTP selection block."""
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('host C compiler unavailable')
        selection = block(postimage(SELECTION),
                          '\tif (devinfo->settings->board_type && devinfo->otp.valid) {')
        harness = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define GFP_KERNEL 0
#define PCIE 0
#define kfree free
#define brcmf_dbg(...) ((void)0)
static int allocation_failure;
static char *devm_kasprintf(void *dev, int flags, const char *format, ...) {
    (void)dev; (void)flags;
    if (allocation_failure) return NULL;
    char *result = malloc(512);
    va_list args; va_start(args, format); vsnprintf(result, 512, format, args); va_end(args);
    return result;
}
struct device { int unused; };
struct brcmf_otp_params { int valid; const char *module, *vendor, *version; };
struct settings { const char *board_type, *antenna_sku; };
struct request { const char *board_types[8]; };
struct info { struct settings *settings; struct brcmf_otp_params otp; struct { struct device dev; } *pdev; };
static struct request *select_names(struct info *devinfo) {
    struct request *fwreq = calloc(1, sizeof(*fwreq));
''' + selection + r''' else {
        fwreq->board_types[0] = devinfo->settings->board_type;
    }
    return fwreq;
}
static void expect(struct request *r, const char **names) {
    int i = 0;
    for (; names[i]; i++) assert(r->board_types[i] && !strcmp(r->board_types[i], names[i]));
    assert(!r->board_types[i]);
}
int main(void) {
    struct { struct device dev; } pdev;
    struct settings settings = { "apple,j71b", NULL };
    struct info info = { &settings, { 1, "YSBC", "u", "4.3" }, (void *)&pdev };
    /* No antenna SKU (iPad): OTP names without SKU-specific entries. */
    const char *nosku[] = { "apple,j71b-YSBC-u-4.3", "apple,j71b-YSBC-u", "apple,j71b-YSBC",
                            "apple,j71b", NULL };
    expect(select_names(&info), nosku);
    /* With a SKU (Macs, J172): upstream's order unchanged. */
    settings.antenna_sku = "X3";
    const char *sku[] = { "apple,j71b-YSBC-u-4.3-X3", "apple,j71b-YSBC-u-4.3", "apple,j71b-YSBC-u",
                          "apple,j71b-YSBC", "apple,j71b-X3", "apple,j71b", NULL };
    expect(select_names(&info), sku);
    /* No valid OTP: board type only. */
    info.otp.valid = 0;
    const char *bare[] = { "apple,j71b", NULL };
    expect(select_names(&info), bare);
    info.otp.valid = 1;
    allocation_failure = 1;
    assert(select_names(&info) == NULL);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'profiles.c'
            binary = Path(directory) / 'profiles'
            source.write_text(harness)
            subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_loader_patches_are_device_generic(self):
        patches = sorted((ROOT / 'boot/m1n1/patches').glob('*.patch'))
        self.assertTrue(patches)
        for path in patches:
            added = [line for line in path.read_text().splitlines()
                     if line.startswith('+') and not line.startswith('+++')]
            # Board data comes from the ADT and DT aliases, never board compatibles.
            self.assertFalse([line for line in added if 'check_compatible' in line], path.name)
            self.assertFalse([line for line in added if 'cal_len !=' in line], path.name)

    def test_existing_device_match_names_not_removed(self):
        for path in series_patches('j71b'):
            before, after = [], []
            for line in path.read_text().splitlines():
                # Match table strings, not branches or comments mentioning compatibles.
                if '.compatible = ' not in line:
                    continue
                names = re.findall(r'\.compatible = "([^"]+)"', line)
                if line.startswith('-'):
                    before.extend(names)
                elif line.startswith(('+', ' ')):
                    after.extend(names)
            self.assertFalse(set(before) - set(after), (path.name, set(before) - set(after)))


if __name__ == '__main__':
    unittest.main()

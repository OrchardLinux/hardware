"""Firmware/profile contracts and executable tests of the patched C selection blocks.

These are host-side regression tests, not proof of radio initialization.
"""
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'kernel/patches/j71b'


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
        self.source = postimage(PATCHES / '0002-j71b-wifi-driver.patch')

    def test_all_ipsw_profiles_have_separate_pinned_destinations(self):
        profiles = {'u-4.3': '9b05b7a2ff1b0baea5b8cb8240d1aedbfcdabafa2604742a72b5fe4a15f1b3ca',
                    'u-4.1': '9b05b7a2ff1b0baea5b8cb8240d1aedbfcdabafa2604742a72b5fe4a15f1b3ca',
                    'm-2.5': '0c83b29dd420c77eef163450aef451261dcebc484f6a2f16627f5a4f3cd52f72',
                    'm-2.3': '0c83b29dd420c77eef163450aef451261dcebc484f6a2f16627f5a4f3cd52f72'}
        nvram = [row for row in self.recipe['files'] if row['name'].endswith('.txt')]
        self.assertEqual(len(nvram), len(profiles))
        for profile, digest in profiles.items():
            vendor, version = profile.split('-')
            self.assertIn({'name': f'P-figaroa_M-YSBC_V-{vendor}__m-{version}.txt',
                           'sha256': digest,
                           'destination': 'lib/firmware/brcm/brcmfmac4355c1-pcie.apple,j71b-YSBC-' + profile + '.txt'}, nvram)
        self.assertFalse(any(row['destination'].endswith('apple,j71b.txt') for row in nvram))

    def test_exact_nvram_has_no_board_or_platform_fallback(self):
        self.assertIn('flags = BRCMF_FW_REQF_EXACT;', self.source)
        exact = self.source.index('if (cur->flags & BRCMF_FW_REQF_EXACT)\n\t\treturn request_firmware')
        self.assertLess(exact, self.source.index('/* Files can be board-specific'))
        self.assertIn('if (cur->flags & BRCMF_FW_REQF_EXACT)\n\t\t\tgoto fail;\n\t\tdata = bcm47xx_nvram_get_contents', self.source)
        self.assertEqual(self.source.count('fwreq ? PTR_ERR(fwreq) : -ENOMEM'), 2)

    def test_selection_c(self):
        """Compile and execute the actual added guard and path-construction C."""
        cc = shutil.which('cc')
        if not cc:
            self.skipTest('host C compiler unavailable')
        helper = block(self.source, 'static bool brcmf_j71b_otp_component')
        guard = block(self.source, '\tif (of_machine_is_compatible("apple,j71b") &&\n\t    (!devinfo->fwseed')
        selection = block(self.source, '\tif (of_machine_is_compatible("apple,j71b")) {\n\t\tchar *path;')
        selection += ' else { fwreq->items[BRCMF_PCIE_FW_NVRAM].flags = BRCMF_FW_REQF_OPTIONAL; }'
        harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <errno.h>
#define BRCMF_PCIE_FW_NVRAM 0
#define BRCMF_FW_REQF_OPTIONAL 1
#define BRCMF_FW_REQF_EXACT 2
#define GFP_KERNEL 0
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define brcmf_err(...) ((void)0)
#define kfree free
static bool machine, allocation_failure;
static bool of_machine_is_compatible(const char *s) { (void)s; return machine; }
static char *devm_kasprintf(void *dev, int flags, const char *format, ...) {
    (void)dev; (void)flags;
    if (allocation_failure) return NULL;
    char *result = malloc(512);
    va_list args; va_start(args, format); vsnprintf(result, 512, format, args); va_end(args);
    return result;
}
struct request { struct { const char *path; int flags; } items[1]; };
struct settings { const char *board_type; };
struct pci { int dev; };
struct info {
    bool fwseed;
    struct { bool valid; const char *module, *vendor, *version; } otp;
    struct settings *settings;
    struct pci *pdev;
    const char *nvram_name;
};
''' + helper + '\nstatic struct request *select_profile(struct info *devinfo) {\n' + guard + r'''
    struct request *fwreq = calloc(1, sizeof(*fwreq));
    fwreq->items[0].path = devinfo->nvram_name;
''' + selection + r'''
    return fwreq;
}
int main(void) {
    struct settings settings = { "apple,j71b" };
    struct pci pci = {0};
    struct info info = { true, {true, "YSBC", "u", "4.3"}, &settings, &pci,
                        "brcm/brcmfmac4355c1-pcie.txt" };
    const char *vendors[] = {"u", "u", "m", "m"};
    const char *versions[] = {"4.3", "4.1", "2.5", "2.3"};
    machine = true;
    for (int n = 0; n < 4; n++) {
        info.otp.vendor = vendors[n]; info.otp.version = versions[n];
        struct request *r = select_profile(&info);
        char expected[512];
        snprintf(expected, sizeof(expected), "brcm/brcmfmac4355c1-pcie.apple,j71b-YSBC-%s-%s.txt", vendors[n], versions[n]);
        assert(r && !strcmp(r->items[0].path, expected));
        assert(r->items[0].flags == BRCMF_FW_REQF_EXACT);
        free((void *)r->items[0].path); free(r);
    }
    /* Future profiles request their own file, never a known unit's profile. */
    info.otp.version = "9.9";
    struct request *r = select_profile(&info);
    assert(strstr(r->items[0].path, "-m-9.9.txt"));
    free((void *)r->items[0].path); free(r);
    info.otp.module = "../YSBC";
    assert(select_profile(&info) == ERR_PTR(-EINVAL));
    info.otp.module = "";
    assert(select_profile(&info) == ERR_PTR(-EINVAL));
    info.otp.module = "YSBC";
    info.otp.valid = false;
    assert(select_profile(&info) == ERR_PTR(-EINVAL));
    info.otp.valid = true; info.fwseed = false;
    assert(select_profile(&info) == ERR_PTR(-EINVAL));
    info.fwseed = true; settings.board_type = NULL;
    assert(select_profile(&info) == ERR_PTR(-EINVAL));
    settings.board_type = "apple,rudderb";
    assert(select_profile(&info) == ERR_PTR(-EINVAL));
    settings.board_type = "apple,j71b";
    allocation_failure = true;
    assert(select_profile(&info) == NULL);
    /* Other boards retain upstream optional NVRAM and board fallback. */
    machine = false; info.otp.valid = false;
    r = select_profile(&info);
    assert(r->items[0].path == info.nvram_name);
    assert(r->items[0].flags == BRCMF_FW_REQF_OPTIONAL);
    free(r);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'profiles.c'
            binary = Path(directory) / 'profiles'
            source.write_text(harness)
            subprocess.run([cc, '-std=c11', '-Wall', '-Wextra', '-Werror', str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_non_j71b_dma_direction_preserved(self):
        self.assertIn('of_machine_is_compatible("apple,j71b") ?\n\t\t\t\t DMA_BIDIRECTIONAL : DMA_FROM_DEVICE', self.source)

    def test_no_single_unit_calibration_size_or_diagnostic_dependency(self):
        loader = (ROOT / 'boot/m1n1/patches/j71b/0001-j71b-boot-support.patch').read_text()
        self.assertNotIn('cal_len != 1780', loader)
        self.assertIn('!cal_len ||', loader)
        bt = (PATCHES / '0003-j71b-bluetooth-driver.patch').read_text()
        self.assertNotIn('hci_h4.c', bt)
        self.assertNotIn('-\t\t\tpm_runtime_dont_use_autosuspend', bt)

    def test_existing_device_match_names_not_removed(self):
        for path in sorted(PATCHES.glob('*.patch')):
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

"""Execute the loader handoff against synthetic 32-bit boot clock tables."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'boot/m1n1/patches/orchard/0005-kboot-Fill-the-AP-NCO-reference-clock-from-the-boot-ADT.patch'


class NCOHandoff(unittest.TestCase):
    def test_boot_clock_width_index_and_missing_data(self):
        compiler = shutil.which('cc')
        if not compiler:
            self.skipTest('host C compiler unavailable')
        text = '\n'.join(line[1:] for line in PATCH.read_text().splitlines()
                         if line.startswith('+') and not line.startswith('+++'))
        start = text.index('static int dt_set_nco_reference(void)')
        function = text[start:text.index('\n}', start) + 2]
        harness = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
static void *dt, *adt;
static u32 ids[2] = {0x10d, 0x10e}, rates[32];
static u32 ids_len = sizeof(ids), rates_len = sizeof(rates);
static int alias = 1, provider = 2, table = 3, writes, adt_calls;
static int no_ids, no_rates;
static u32 written;
#define bail(...) do { return -1; } while (0)
static int fdt_path_offset(void *blob, const char *path)
{
    (void)blob;
    assert(!strcmp(path, "nco-ref"));
    return alias;
}
static int adt_path_offset(void *blob, const char *path)
{
    (void)blob;
    adt_calls++;
    return !strcmp(path, "/arm-io/nco") ? provider : table;
}
static const void *adt_getprop(void *blob, int node, const char *name, u32 *len)
{
    (void)blob;
    if (node == provider && !strcmp(name, "clock-ids")) {
        *len = ids_len;
        return no_ids ? NULL : ids;
    }
    assert(node == table && !strcmp(name, "clock-frequencies"));
    *len = rates_len;
    return no_rates ? NULL : rates;
}
static int fdt_setprop_u32(void *blob, int node, const char *name, u32 value)
{
    (void)blob;
    assert(node == alias && !strcmp(name, "clock-frequency"));
    writes++;
    written = value;
    return 0;
}
'''
        cases = r'''
int main(void)
{
    for (unsigned i = 0; i < 32; i++)
        rates[i] = 123456789;
    rates[13] = 720000000;
    rates[14] = 48000000;
    assert(!dt_set_nco_reference() && writes == 1 && written == 720000000);
    /* The last 32-bit entry must remain addressable. */
    ids[0] = 0x11f;
    assert(!dt_set_nco_reference() && writes == 2 && written == rates[31]);
    /* Missing/unusable boot data must preserve boot and leave the DT alone. */
    ids[0] = 0x120;
    assert(!dt_set_nco_reference() && writes == 2);
    ids[0] = 0xff;
    assert(!dt_set_nco_reference() && writes == 2);
    ids[0] = 0x10d;
    ids_len = 0;
    assert(!dt_set_nco_reference() && writes == 2);
    ids_len = sizeof(ids);
    rates_len = 13 * sizeof(u32);
    assert(!dt_set_nco_reference() && writes == 2);
    rates_len = sizeof(rates);
    no_rates = 1;
    assert(!dt_set_nco_reference() && writes == 2);
    no_rates = 0;
    no_ids = 1;
    assert(!dt_set_nco_reference() && writes == 2);
    no_ids = 0;
    rates[13] = 0;
    assert(!dt_set_nco_reference() && writes == 2);
    provider = -1;
    assert(!dt_set_nco_reference() && writes == 2);
    alias = -1;
    adt_calls = 0;
    assert(!dt_set_nco_reference() && writes == 2 && adt_calls == 0);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'handoff.c'
            executable = Path(directory) / 'handoff'
            source.write_text(harness + function + cases)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(executable)], check=True, capture_output=True)
            subprocess.run([str(executable)], check=True, capture_output=True)


if __name__ == '__main__':
    unittest.main()

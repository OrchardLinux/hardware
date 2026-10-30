"""Execute N61 packet and SPI transport code with synthetic calibration."""
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / 'kernel/patches/drivers/input/0006-Input-apple_z2-add-n61-gen1.patch'
BINDING = ROOT / 'kernel/patches/drivers/input/0005-dt-bindings-input-apple-z2-add-n61.patch'


def postimages(patch=PATCH):
    files, current = {}, None
    for line in patch.read_text().splitlines():
        if line.startswith('+++ b/'):
            current = line[6:]
            files[current] = []
        elif current is not None and line[:1] in (' ', '+'):
            files[current].append(line[1:])
    return {name: '\n'.join(lines) for name, lines in files.items()}


def function(source, label):
    start = source.index(label)
    opening = source.index('{', start)
    depth = 0
    for position in range(opening, len(source)):
        depth += source[position] == '{'
        depth -= source[position] == '}'
        if not depth:
            return source[start:position + 1]
    raise ValueError('Incomplete C function')


class N61TouchPackets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc') or shutil.which('clang')
        if compiler is None:
            raise unittest.SkipTest('A host C compiler is required for N61 wire tests')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        files = postimages()
        (root / 'apple_z2_n61.h').write_text(files['drivers/input/touchscreen/apple_z2_n61.h'])
        linux = root / 'linux'
        linux.mkdir()
        (linux / 'types.h').write_text('#include <stdbool.h>\n#include <stddef.h>\n#include <stdint.h>\ntypedef uint8_t u8; typedef uint16_t u16; typedef uint32_t u32;\n')
        # glibc's errno.h includes linux/errno.h; define kernel values directly.
        (linux / 'errno.h').write_text('#define EIO 5\n#define EINVAL 22\n#define EMSGSIZE 90\n')
        (linux / 'string.h').write_text('#include <string.h>\n')
        (linux / 'unaligned.h').write_text('static inline void put_unaligned_be16(unsigned n, void *p) { unsigned char *b=p; b[0]=n>>8; b[1]=n; }\nstatic inline void put_unaligned_le16(unsigned n, void *p) { unsigned char *b=p; b[0]=n; b[1]=n>>8; }\nstatic inline unsigned get_unaligned_be16(const void *p) { const unsigned char *b=p; return (b[0]<<8)|b[1]; }\n')
        transport = function(files['drivers/input/touchscreen/apple_z2.c'],
                             'static int apple_z2_send_n61_blob(')
        program = r'''
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "apple_z2_n61.h"
#define DIV_ROUND_UP(n,d) (((n)+(d)-1)/(d))
#define min_t(t,a,b) ((t)((a)<(b)?(a):(b)))
#define dev_dbg(...) ((void)0)
struct spi_device { int unused; };
struct spi_transfer {
    const void *tx_buf; void *rx_buf; u32 len;
    unsigned bits_per_word, speed_hz, cs_change;
};
struct apple_z2 {
    struct spi_device *spidev; u8 *tx_buf; u8 *rx_buf;
    int index_parity; bool runtime_queue_empty, n61;
};
static bool apple_z2_is_n61(struct apple_z2 *z) { return z->n61; }
static bool apple_z2_uses_gen1_data(struct apple_z2 *z) { (void)z; return true; }
static unsigned calls, first_count, first_total, acknowledgements, fail_call;
static int spi_sync_transfer(struct spi_device *dev, struct spi_transfer *x, unsigned count)
{
    unsigned i, total=0;
    (void)dev;
    calls++;
    for(i=0;i<count;i++) {
        assert(x[i].bits_per_word==8 && x[i].speed_hz==500000);
        assert(x[i].len<=APPLE_Z2_N61_CHUNK && !x[i].cs_change);
        total+=x[i].len;
    }
    if(calls==1) { first_count=count; first_total=total; }
    else if(count==1 && x[0].len==2) {
        const u8 *ack=x[0].tx_buf;
        assert(ack[0]==0x1a && ack[1]==0xa1);
        acknowledgements++;
    }
    return calls==fail_call ? -EIO : 0;
}
static void apple_z2_apply_z2_delays(struct apple_z2 *z) { (void)z; }
static void apple_z2_post_ack_delay(struct apple_z2 *z) { (void)z; }
'''
        program += transport
        empty = function(files['drivers/input/touchscreen/apple_z2.c'],
                         'if (apple_z2_uses_gen1_data(z2) && !payload_len)')
        program += '\nstatic int empty_result(struct apple_z2 *z2, u16 payload_len) {\n'
        program += empty + '\nreturn -1;\n}\n'
        program += r'''
int main(int argc, char **argv)
{
    u8 cal[1024], out[1056], tx[16], rx[8192], firmware[65320];
    struct spi_device spi={0};
    struct apple_z2 z={.spidev=&spi,.tx_buf=tx,.rx_buf=rx};
    u8 init[]={0x1a,0xa1,0x18,0xe1,0x18,0xe1,0x18,0xe1,
               0x18,0xe1,0x18,0xe1,0x18,0xe1,0x18,0xe1};
    u8 execute[]={0x1d,0x53,0x34,0,0x10,0,0,1,0,0,0,0x45};
    unsigned i; int n;
    assert(argc==2);
    for(i=0;i<sizeof(cal);i++) cal[i]=i;
    if(!strcmp(argv[1],"golden")) {
        n=apple_z2_n61_calibration_packet(out,sizeof(out),cal,16,0x10009000);
        assert(n==32);
        for(i=0;i<(unsigned)n;i++) printf("%02x",out[i]);
        puts("");
        return 0;
    }
    if(!strcmp(argv[1],"bounds")) {
        assert(apple_z2_n61_calibration_target(0x10009000,1024,0));
        assert(apple_z2_n61_calibration_target(0x10009600,128,9));
        assert(!apple_z2_n61_calibration_target(0x10009000,1025,0));
        assert(!apple_z2_n61_calibration_target(0x10009600,129,9));
        assert(!apple_z2_n61_calibration_target(0x10009000,128,9));
        assert(!apple_z2_n61_calibration_target(0x10009000,1024,1));
        memset(out,0xaa,sizeof(out));
        assert(apple_z2_n61_calibration_packet(out,sizeof(out),cal,1024,0x10009000)==1040);
        assert(out[4]==1 && out[5]==0 && out[1040]==0xaa);
        assert(apple_z2_n61_calibration_packet(out,1039,cal,1024,0x10009000)==-EMSGSIZE);
        assert(apple_z2_n61_calibration_packet(out,sizeof(out),cal,0,0x10009000)==-EINVAL);
        assert(apple_z2_n61_calibration_packet(out,sizeof(out),cal,129,0x10009600)==-EINVAL);
        assert(apple_z2_n61_calibration_packet(out,sizeof(out),cal,1,0)==-EINVAL);
        assert(apple_z2_n61_calibration_packet(out,sizeof(out),cal,1,0x10009000)==20);
        assert(out[4]==0 && out[5]==1 && out[12]==0 && out[14]==0 && out[15]==0);
        return 0;
    }
    if(!strcmp(argv[1],"transport")) {
        assert(apple_z2_send_n61_blob(&z,firmware,sizeof(firmware),false)==0);
        assert(calls==2 && first_count==4 && first_total==65320 && acknowledgements==1);
        calls=acknowledgements=0;
        fail_call=1;
        assert(apple_z2_send_n61_blob(&z,firmware,sizeof(firmware),false)==-EIO);
        assert(calls==1 && acknowledgements==0);
        calls=fail_call=0;
        assert(apple_z2_send_n61_blob(&z,firmware,65537,false)==-EINVAL && calls==0);
        assert(apple_z2_send_n61_blob(&z,(const u8 *)"\x19\xc1",2,true)==0);
        assert(calls==1 && first_count==1 && first_total==2);
        calls=0;
        assert(apple_z2_send_n61_blob(&z,init,sizeof(init),true)==0 && calls==1);
        calls=0;
        assert(apple_z2_send_n61_blob(&z,execute,sizeof(execute),true)==0 && calls==1);
        calls=0;
        assert(apple_z2_send_n61_blob(&z,init,4,true)==-EINVAL && calls==0);
        return 0;
    }
    if(!strcmp(argv[1],"longread")) {
        memset(out,0xaa,sizeof(out));
        apple_z2_n61_long_read_request(out,0xd9,16);
        assert(out[0]==0xe7 && out[1]==0xd9 && out[2]==1);
        assert(out[3]==16 && out[4]==0 && out[19]==0xd0 && out[20]==1);
        assert(out[21]==0xaa);
        for(i=5;i<19;i++) assert(out[i]==0);
        apple_z2_n61_long_read_request(out,1,12);
        assert(out[15]==0xf4 && out[16]==0 && out[2]==1);
        apple_z2_n61_short_read_request(out,0xaf,1);
        assert(out[0]==0xe6 && out[1]==0xaf && out[2]==1 && out[3]==1);
        assert(out[14]==0x96 && out[15]==1);
        return 0;
    }
    if(!strcmp(argv[1],"rom")) {
        static const u16 allowed[]={0x18e1,0x1aa1,0x1f01,0x4879,0x4969,0x4ad1,0x4bc1};
        unsigned left,right,valid=0;
        for(i=0;i<65536;i++) valid+=apple_z2_n61_rom_word_valid(i);
        assert(valid==7);
        for(left=0;left<7;left++) for(right=0;right<7;right++) {
            put_unaligned_be16(allowed[left],out);
            put_unaligned_be16(allowed[right],out+2);
            assert(apple_z2_n61_rom_ready(out,4));
            assert(!apple_z2_n61_rom_ready(out,3));
        }
        put_unaligned_be16(0,out);
        assert(!apple_z2_n61_rom_ready(out,16));
        put_unaligned_be16(0x18e1,out);
        put_unaligned_be16(0xffff,out+2);
        assert(!apple_z2_n61_rom_ready(out,16));
        return 0;
    }
    if(!strcmp(argv[1],"interrupt")) {
        memset(out,0xaa,sizeof(out));
        assert(apple_z2_n61_interrupt_request(out,61,1)==0);
        assert(out[0]==0xeb && out[1]==1 && out[2]==1);
        for(i=3;i<59;i++) assert(out[i]==0);
        assert(out[59]==0xed && out[60]==0 && out[61]==0xaa);
        assert(apple_z2_n61_interrupt_request(out,16,2)==0);
        assert(out[14]==0xee && out[15]==0);
        assert(apple_z2_n61_interrupt_request(rx,sizeof(rx),1)==0);
        assert(rx[8190]==0xed && rx[8191]==0 && rx[14]==0);
        assert(apple_z2_n61_interrupt_request(out,15,1)==-EINVAL);
        assert(apple_z2_n61_interrupt_request(out,8193,1)==-EINVAL);
        assert(apple_z2_n61_interrupt_request(out,61,0)==-EINVAL);
        assert(apple_z2_n61_interrupt_request(out,61,3)==-EINVAL);
        return 0;
    }
    if(!strcmp(argv[1],"empty")) {
        z.n61=true;
        assert(empty_result(&z,0)==0 && z.runtime_queue_empty && z.index_parity==1);
        z.runtime_queue_empty=false;
        assert(empty_result(&z,0)==0 && z.runtime_queue_empty && z.index_parity==0);
        z.runtime_queue_empty=false;
        assert(empty_result(&z,12)==-1 && !z.runtime_queue_empty && z.index_parity==0);
        z.n61=false;
        assert(empty_result(&z,0)==0 && z.runtime_queue_empty && z.index_parity==0);
        return 0;
    }
    return 2;
}
'''
        source = root / 'test.c'
        source.write_text(program)
        cls.binary = root / 'n61-wire-test'
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        '-I', str(root), str(source), '-o', str(cls.binary)],
                       check=True, capture_output=True, text=True)

    def test_gen1_middle_endian_packet_matches_golden_bytes(self):
        output = subprocess.check_output([self.binary, 'golden'], text=True).strip()
        self.assertEqual(output, '18e1300100049000100000a4010003020504070609080b0a0d0c0f0e00780000')

    def test_provider_addresses_lengths_and_full_word_count(self):
        subprocess.run([self.binary, 'bounds'], check=True)

    def test_chunked_message_retains_cs_and_distinct_init_ack_rules(self):
        subprocess.run([self.binary, 'transport'], check=True)

    def test_n61_e6_e7_reads_keep_the_phase_zero_checksum(self):
        subprocess.run([self.binary, 'longread'], check=True)

    def test_exact_rom_words_accept_all_pairs_and_refuse_truncation(self):
        subprocess.run([self.binary, 'rom'], check=True)

    def test_eb_checksum_uses_the_wire_tail_for_the_observed_61_byte_read(self):
        subprocess.run([self.binary, 'interrupt'], check=True)

    def test_n61_successful_empty_read_advances_tag_without_changing_j71b(self):
        subprocess.run([self.binary, 'empty'], check=True)


class N61TouchPolicy(unittest.TestCase):
    def test_prox_provider_is_appended_and_loader_forwards_per_unit_data(self):
        files = postimages()
        driver = files['drivers/input/touchscreen/apple_z2.c']
        self.assertIn('APPLE_Z2_CAL_PROX = 9', driver)
        self.assertIn('"apple,z2-prox-cal-blob"', driver)
        binding = postimages(BINDING)['Documentation/devicetree/bindings/input/touchscreen/apple,z2-multitouch.yaml']
        for supply in ('ana-supply', 'core-supply', 'clocks'):
            self.assertIn('        - ' + supply, binding)
        self.assertNotIn('apple,boot-power-preserved', binding)
        self.assertIn('maxItems: 1024', binding)
        patch = (ROOT / 'boot/m1n1/patches/orchard/0003-Forward-touchscreen-proximity-calibration.patch').read_text()
        self.assertIn('{"prox-calibration", "apple,z2-prox-cal-blob"}', patch)

    def test_n61_framing_preserves_j71b_irq_policy_and_each_boards_timing(self):
        compiler = shutil.which('cc') or shutil.which('clang')
        if compiler is None:
            self.skipTest('A host C compiler is required')
        n61 = postimages()['drivers/input/touchscreen/apple_z2.c']
        shared_patch = ROOT / 'kernel/patches/drivers/input/0004-Input-apple_z2-Describe-generation-1-timing-in-match.patch'
        j71b = postimages(shared_patch)['drivers/input/touchscreen/apple_z2.c']
        code = r'''
#include <assert.h>
#include <stdbool.h>
enum { APPLE_Z2_VARIANT_J71B, APPLE_Z2_VARIANT_N61, APPLE_Z2_VARIANT_OTHER };
struct apple_z2 { int variant; };
struct apple_z2_chip_info {
    const char *name; int variant; unsigned cs_setup_us, cs_hold_us, cs_inactive_us;
};
static bool apple_z2_is_j71b(const struct apple_z2 *z) { return z->variant == APPLE_Z2_VARIANT_J71B; }
'''
        for label in ('static bool apple_z2_is_n61(', 'static bool apple_z2_is_gen1(',
                      'static bool apple_z2_uses_gen1_data('):
            code += function(n61, label) + '\n'
        code += function(n61, 'static const struct apple_z2_chip_info apple_z2_n61_info') + ';\n'
        code += function(j71b, 'static const struct apple_z2_chip_info apple_z2_j71b_info') + ';\n'
        code += r'''
int main(void) {
    struct apple_z2 j = { APPLE_Z2_VARIANT_J71B }, n = { APPLE_Z2_VARIANT_N61 };
    struct apple_z2 other = { APPLE_Z2_VARIANT_OTHER };
    assert(apple_z2_is_gen1(&j) && !apple_z2_is_gen1(&n));
    assert(apple_z2_uses_gen1_data(&j) && apple_z2_uses_gen1_data(&n));
    assert(!apple_z2_uses_gen1_data(&other));
    assert(apple_z2_j71b_info.cs_setup_us == 1000 && apple_z2_j71b_info.cs_hold_us == 0);
    assert(apple_z2_j71b_info.cs_inactive_us == 1000);
    assert(apple_z2_n61_info.cs_setup_us == 1000 && apple_z2_n61_info.cs_hold_us == 1000);
    assert(apple_z2_n61_info.cs_inactive_us == 1000);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'shared.c'
            binary = Path(temporary) / 'shared'
            source.write_text(code)
            subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror',
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([binary], check=True)


class N61TouchPower(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc') or shutil.which('clang')
        if compiler is None:
            raise unittest.SkipTest('A host C compiler is required for N61 power tests')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        patch = ROOT / 'kernel/patches/drivers/regulator/0002-regulator-apple-add-n61-touch.patch'
        cls.files = postimages(patch)
        regulator = cls.files['drivers/regulator/apple-pmic-touch-regulator.c']
        shared = postimages(ROOT / 'kernel/patches/import/pauli1go/0064-regulator-add-Apple-PMIC-touchscreen-supply-driver.patch')
        shared = shared['drivers/regulator/apple-pmic-touch-regulator.c']
        actual = '\n'.join([
            function(regulator, 'static const struct regulator_desc apple_d2186_touch_core') + ';',
            function(shared, 'static const struct regulator_desc apple_chestnut_touch_hv =') + ';',
        ])
        program = r'''
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <string.h>
#define THIS_MODULE 0
#define REGULATOR_VOLTAGE 1
struct regulator_dev;
struct regulator_ops {
    int (*enable)(struct regulator_dev *);
    int (*disable)(struct regulator_dev *);
    int (*is_enabled)(struct regulator_dev *);
};
struct regulator_desc {
    const char *name; const struct regulator_ops *ops;
    int type, owner; unsigned enable_reg, enable_mask, enable_val, disable_val;
    unsigned enable_time, off_on_delay;
};
struct regmap { unsigned value, calls, fail, reg; };
struct regulator_dev { struct regmap *map; const struct regulator_desc *desc; };
static int regmap_update_bits(struct regmap *r, unsigned reg, unsigned mask, unsigned value)
{
    r->calls++;
    r->reg=reg;
    if(r->calls==r->fail) return -EIO;
    r->value=(r->value&~mask)|(value&mask);
    return 0;
}
static int regmap_read(struct regmap *r, unsigned reg, unsigned *value)
{
    r->reg=reg;
    if(r->fail) return -EIO;
    *value=r->value;
    return 0;
}
static int regulator_enable_regmap(struct regulator_dev *r)
{ return regmap_update_bits(r->map,r->desc->enable_reg,r->desc->enable_mask,r->desc->enable_val); }
static int regulator_disable_regmap(struct regulator_dev *r)
{ return regmap_update_bits(r->map,r->desc->enable_reg,r->desc->enable_mask,r->desc->disable_val); }
static int regulator_is_enabled_regmap(struct regulator_dev *r)
{
    unsigned value;
    int error=regmap_read(r->map,r->desc->enable_reg,&value);
    return error?error:(value&r->desc->enable_mask)==r->desc->enable_val;
}
static const struct regulator_ops apple_pmic_touch_regulator_ops={
    regulator_enable_regmap,regulator_disable_regmap,regulator_is_enabled_regmap
};
'''
        program += actual
        program += r'''
int main(int argc, char **argv)
{
    unsigned initial;
    struct regmap map={0};
    struct regulator_dev r={&map,&apple_chestnut_touch_hv};
    assert(argc==2);
    if(!strcmp(argv[1],"chestnut")) {
        assert(r.desc->enable_time==1000 && r.desc->off_on_delay==1000);
        for(initial=0;initial<256;initial++) {
            map=(struct regmap){.value=initial};
            assert(r.desc->ops->enable(&r)==0);
            assert(map.value==(initial|0x10) && map.reg==5 && map.calls==1);
            assert(r.desc->ops->is_enabled(&r)==1);
            assert(r.desc->ops->disable(&r)==0);
            assert(map.value==(initial&~0x10U));
            assert(r.desc->ops->is_enabled(&r)==0);
        }
        map=(struct regmap){.value=0x0f};
        assert(r.desc->ops->enable(&r)==0 && map.value==0x1f);
        assert(r.desc->ops->disable(&r)==0 && map.value==0x0f);
        return 0;
    }
    if(!strcmp(argv[1],"failures")) {
        map=(struct regmap){.value=0x0f,.fail=1};
        assert(r.desc->ops->enable(&r)==-EIO && map.calls==1 && map.value==0x0f);
        map=(struct regmap){.value=0xff,.fail=1};
        assert(r.desc->ops->is_enabled(&r)==-EIO);
        return 0;
    }
    if(!strcmp(argv[1],"core")) {
        r.desc=&apple_d2186_touch_core;
        for(initial=0;initial<256;initial++) {
            map=(struct regmap){.value=initial};
            assert(r.desc->ops->enable(&r)==0 && map.value==(initial|2) && map.reg==0x85);
            assert(r.desc->ops->disable(&r)==0 && map.value==(initial&~2U));
        }
        return 0;
    }
    return 2;
}
'''
        source = root / 'test.c'
        source.write_text(program)
        cls.binary = root / 'n61-power-test'
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(source), '-o', str(cls.binary)], check=True,
                       capture_output=True, text=True)

    def test_chestnut_preserves_every_unowned_bit_and_lcd_baseline(self):
        subprocess.run([self.binary, 'chestnut'], check=True)

    def test_touch_switch_propagates_regmap_failures(self):
        subprocess.run([self.binary, 'failures'], check=True)

    def test_d2186_owns_only_ldo14_at_the_exact_register(self):
        subprocess.run([self.binary, 'core'], check=True)

    def test_qualified_n61_uses_explicit_providers_and_enables_touch(self):
        files = postimages(ROOT / 'kernel/patches/devices/t7000/0002-arm64-dts-apple-wire-n61-touch.patch')
        board = files['arch/arm64/boot/dts/apple/t7000-n61.dts']
        self.assertIn('ana-supply = <&touch_ana>', board)
        self.assertIn('core-supply = <&touch_core>', board)
        self.assertIn('touch_ana: regulator-touch-hv', board)
        self.assertIn('compatible = "apple,chestnut-touch-hv-regulator"', board)
        touch = re.search(r'touchscreen: touchscreen@0 \{(.*?)\n\t\};', board, re.S)[1]
        self.assertIn('status = "okay"', touch)
        self.assertIn('reg = <0x27>', board)
        clock = files['arch/arm64/boot/dts/apple/t7000-pmgr.dtsi']
        self.assertIn('reg = <0x1e000 0x4>', clock)
        self.assertIn('clocks = <&clkref>', clock)
        self.assertIn('clock-frequency = <32768>', clock)
        self.assertIn('status = "disabled"', clock)


class N61Startup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc') or shutil.which('clang')
        if compiler is None:
            raise unittest.SkipTest('A host C compiler is required for N61 startup tests')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        driver = postimages()['drivers/input/touchscreen/apple_z2.c']
        program = r'''
#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
#define APPLE_Z2_STATE_POWERED 1
#define APPLE_Z2_STATE_HBPP_READY 2
#define APPLE_Z2_N61_READY_ATTEMPTS 50
struct spi_device { int irq; };
struct apple_z2 {
    struct spi_device *spidev; int reset_gpio, boot_irq;
    void *hv_supply, *core_supply;
    bool platform_powered, hv_enabled, core_enabled;
    bool clk_enabled, booted, upload_irq_masked;
    u8 *rx_buf;
    int protocol_state;
};
static char events[256];
static unsigned used, fault, irq_depth, reset_asserted, supply_calls;
static void event(char c) { assert(used<sizeof(events)-1); events[used++]=c; events[used]=0; }
static void gpiod_set_value_cansleep(int gpio, int value)
{ (void)gpio; reset_asserted=value; event(value?'R':'r'); }
static int regulator_enable(void *regulator)
{
    unsigned which=regulator==(void *)1?1:2;
    assert(reset_asserted);
    supply_calls++;
    event(which==1?'A':'C');
    return fault==which?-EIO:0;
}
static void usleep_range(unsigned min, unsigned max)
{
    assert(max>=min && max<=min+1000);
    if(min==5000) event('5');
    else if(min==15000) event('F');
    else if(min==10000) event('T');
    else assert(0);
}
static int apple_z2_enable_clock(struct apple_z2 *z)
{
    assert(z->hv_enabled && z->core_enabled && reset_asserted);
    event('K');
    if(fault==3) return -EIO;
    z->clk_enabled=true;
    return 0;
}
static void apple_z2_platform_power_off(struct apple_z2 *z)
{
    assert(reset_asserted);
    event('O');
    z->platform_powered=false;
    z->clk_enabled=z->hv_enabled=z->core_enabled=false;
}
static void apple_z2_reset_protocol(struct apple_z2 *z) { z->protocol_state=0; }
static void enable_irq(int irq) { (void)irq; assert(irq_depth==1); irq_depth--; event('E'); }
static u16 get_unaligned_be16(const void *p) { const u8 *b=p; return (b[0]<<8)|b[1]; }
static int apple_z2_send_n61_blob(struct apple_z2 *z, const u8 *data, u32 size, bool init)
{
    unsigned i;
    assert(init && z->clk_enabled);
    if(size==2) {
        assert(reset_asserted);
        assert(data[0]==0x19 && data[1]==0xc1);
        event('P');
        return fault==4?-EIO:0;
    }
    assert(size==16 && data[0]==0x1a && data[1]==0xa1);
    for(i=2;i<16;i+=2) assert(data[i]==0x18 && data[i+1]==0xe1);
    if(reset_asserted) { event('Q'); return fault==5?-EIO:0; }
    event('B');
    if(fault==8) return -EIO;
    memset(z->rx_buf,0,16);
    if(fault!=6) { z->rx_buf[0]=0x18; z->rx_buf[1]=0xe1; z->rx_buf[2]=0x1a; z->rx_buf[3]=0xa1; }
    return 0;
}
static int apple_z2_upload_firmware(struct apple_z2 *z)
{
    assert(irq_depth==1 && z->upload_irq_masked);
    event('U');
    if(fault==7) return -EIO;
    z->booted=true;
    z->upload_irq_masked=false;
    enable_irq(z->spidev->irq);
    return 0;
}
'''
        header = postimages()['drivers/input/touchscreen/apple_z2_n61.h']
        program += function(header, 'static inline bool apple_z2_n61_rom_word_valid(')
        program += '\n' + function(header, 'static inline bool apple_z2_n61_rom_ready(')
        program += function(driver, 'static int apple_z2_n61_power_on(')
        program += '\n' + function(driver, 'static int apple_z2_n61_boot(')
        program += r'''
int main(int argc, char **argv)
{
    struct spi_device spi={3};
    u8 rx[16]={0};
    struct apple_z2 z;
    unsigned failure;
    assert(argc==2);
    if(!strcmp(argv[1],"sequence")) {
        z=(struct apple_z2){.spidev=&spi,.hv_supply=(void *)1,.core_supply=(void *)2,.rx_buf=rx};
        irq_depth=1;
        assert(apple_z2_n61_boot(&z)==0);
        assert(!strcmp(events,"RAC5KFPTQrFBUE"));
        assert(z.booted && z.platform_powered && !irq_depth && !reset_asserted);
        return 0;
    }
    if(!strcmp(argv[1],"failures")) {
        for(failure=1;failure<=8;failure++) {
            used=0; events[0]=0; irq_depth=1; fault=failure;
            z=(struct apple_z2){.spidev=&spi,.hv_supply=(void *)1,.core_supply=(void *)2,.rx_buf=rx};
            assert(apple_z2_n61_boot(&z)==(failure==6?-ETIMEDOUT:-EIO));
            assert(irq_depth==1 && reset_asserted);
            assert(!z.hv_enabled && !z.core_enabled && !z.clk_enabled && !z.booted);
            assert(!z.upload_irq_masked && !z.platform_powered && !z.protocol_state);
        }
        return 0;
    }
    return 2;
}
'''
        source = root / 'test.c'
        source.write_text(program)
        cls.binary = root / 'n61-startup-test'
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(source), '-o', str(cls.binary)], check=True,
                       capture_output=True, text=True)

    def test_apple_power_preamble_and_reset_order(self):
        subprocess.run([self.binary, 'sequence'], check=True)

    def test_failure_keeps_reset_asserted_and_balances_irq_masking(self):
        subprocess.run([self.binary, 'failures'], check=True)


if __name__ == '__main__':
    unittest.main()

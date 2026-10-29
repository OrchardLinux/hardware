"""N61 SPI wiring, independent word/mode fields and PIO contracts."""
import importlib.util
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PATCHES = ROOT / 'kernel/patches/devices/t7000'
SPI = ROOT / 'kernel/patches/drivers/spi'


def changes(name):
    """Return the post-image fragments carried by each unified-diff file."""
    files, current = {}, None
    for line in Path(name).read_text().splitlines():
        if line.startswith('+++ b/'):
            current = line[6:]
            files[current] = []
        elif current is not None and line[:1] in (' ', '+'):
            files[current].append(line[1:])
    return {path: '\n'.join(lines) for path, lines in files.items()}


class N61SPI(unittest.TestCase):
    def test_binding_selects_legacy_backend_before_the_generic_fallback(self):
        files = changes(SPI / '0003-spi-apple-add-t7000.patch')
        binding = changes(SPI / '0002-dt-bindings-spi-add-t7000.patch')['Documentation/devicetree/bindings/spi/apple,spi.yaml']
        self.assertIn('apple,t7000-spi', binding)
        controller = files['drivers/spi/spi-apple.c']
        matches = dict(re.findall(r'compatible = "([^"]+)".*?data = &(\w+)', controller))
        self.assertEqual(matches['apple,t7000-spi'], 'apple_spi_hw_t7000')
        self.assertNotEqual(matches['apple,t7000-spi'], matches['apple,t8010-spi'])
        self.assertNotEqual(matches['apple,t7000-spi'], matches['apple,t8103-spi'])

    def test_soc_bus_matches_n61_adt_address_clock_irq_and_power(self):
        files = changes(PATCHES / '0001-arm64-dts-apple-describe-n61-spi2.patch')
        soc = files['arch/arm64/boot/dts/apple/t7000.dtsi']
        clock = re.search(r'spi2_clk: clock-spi2 \{(.*?)\n\t\};', soc, re.S)[1]
        self.assertIn('compatible = "fixed-clock"', clock)
        self.assertRegex(clock, r'clock-frequency\s*=\s*<60000000>')
        bus = re.search(r'spi2: spi@20a088000 \{(.*?)\n\t\t\};', soc, re.S)[1]
        for setting in ('compatible = "apple,t7000-spi", "apple,spi"',
                        'reg = <0x2 0x0a088000 0x0 0x4000>',
                        'interrupts = <AIC_IRQ 154 IRQ_TYPE_LEVEL_HIGH>',
                        'clocks = <&spi2_clk>', 'power-domains = <&ps_spi2>',
                        'status = "disabled"'):
            with self.subTest(setting=setting):
                self.assertIn(setting, bus)

    def test_controller_layer_adds_cs_without_touch_or_dma_wiring(self):
        files = changes(PATCHES / '0001-arm64-dts-apple-describe-n61-spi2.patch')
        self.assertEqual(set(files), {'arch/arm64/boot/dts/apple/t7000.dtsi',
                                      'arch/arm64/boot/dts/apple/t7000-n61.dts'})
        board = files['arch/arm64/boot/dts/apple/t7000-n61.dts']
        bus = re.search(r'&spi2 \{(.*?)\n\};', board, re.S)[1]
        self.assertIn('cs-gpios = <&pinctrl 185 GPIO_ACTIVE_LOW>', bus)
        self.assertIn('status = "okay"', bus)
        self.assertIn('spi2 = &spi2', board)
        # Controller wiring is separate from the qualified board power and
        # touchscreen layer, so it can be reviewed without driving supplies.
        for source in files.values():
            for forbidden in ('dmas =', 'dma-names =', 'touchscreen@', 'reset-gpios =',
                              'core-supply =', 'hv-supply =', 'pinmux =', 'firmware-name ='):
                with self.subTest(forbidden=forbidden):
                    self.assertNotIn(forbidden, source)

    def test_shared_native_series_carries_the_ordered_n61_layers(self):
        spec = importlib.util.spec_from_file_location('n61_spi_series', ROOT / 'kernel/series.py')
        series = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(series)
        expected = sorted(PATCHES.glob('*.patch'))
        for platform in ('apple-4k', 'apple-16k'):
            found = series.patches(platform)
            self.assertEqual([path for path in found if path.parent == PATCHES], expected)


class N61SPIConfig(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        compiler = shutil.which('cc') or shutil.which('clang')
        if compiler is None:
            raise unittest.SkipTest('A host C compiler is required for SPI config checks')
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        root = Path(cls.temporary.name)
        controller = changes(SPI / '0003-spi-apple-add-t7000.patch')['drivers/spi/spi-apple.c']

        def block(label):
            start = controller.index(label)
            opening = controller.index('{', start)
            depth = 0
            for index in range(opening, len(controller)):
                depth += controller[index] == '{'
                depth -= controller[index] == '}'
                if not depth:
                    return controller[start:index + 1]
            raise ValueError('Incomplete native SPI block')

        start = controller.index('\tuse_descriptor = spi->hw->has_descriptor_dma')
        end = controller.index('\n\treg_write(spi, APPLE_SPI_RXCNT', start)
        selection = controller[start:end]
        program = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>
typedef uint32_t u32;
#define BIT(n) (1U<<(n))
#define SPI_CPHA 1
#define SPI_CPOL 2
#define APPLE_SPI_CFG_CPHA BIT(1)
#define APPLE_SPI_CFG_CPOL BIT(2)
#define APPLE_SPI_CFG_T7000_BASE 0x4018
#define APPLE_SPI_T8010_DESC_CFG_DEFAULT 0x2000
#define APPLE_SPI_CLKDIV 0x30
#define APPLE_SPI_WORD_DELAY 0x38
struct spi_device { unsigned mode; };
struct spi_transfer { unsigned unused; };
struct apple_spi_hw {
    bool legacy_status,has_descriptor_dma; unsigned min_clk_div; u32 default_word_delay;
};
struct apple_spi { const struct apple_spi_hw *hw; u32 word_delay; };
static unsigned descriptor_calls,old_config_calls,prepared_descriptor;
static bool apple_spi_t8010_uses_descriptor(struct spi_transfer *t,unsigned words)
{ (void)t; descriptor_calls++; return words<=16; }
static u32 apple_spi_t8010_config(struct spi_device *d,unsigned bits,bool descriptor)
{ (void)d; (void)bits; old_config_calls++; return descriptor?0x601f:0x104018; }
static void reg_write(struct apple_spi *s,unsigned reg,u32 value)
{ (void)s; assert((reg==0x30 && value==120)||(reg==0x38 && value==0)); }
static u32 apple_spi_t8010_word_delay(struct apple_spi *s,struct spi_device *d,bool descriptor)
{ (void)d; (void)descriptor; return s->word_delay; }
static void apple_spi_t8010_prepare_xfer(struct apple_spi *s,bool descriptor)
{ (void)s; prepared_descriptor=descriptor; }
'''
        program += block('static const struct apple_spi_hw apple_spi_hw_t8010') + ';\n'
        program += block('static const struct apple_spi_hw apple_spi_hw_t7000') + ';\n'
        program += block('static u32 apple_spi_t7000_config(')
        program += r'''
static u32 select_config(struct apple_spi *spi,struct spi_device *device,
                         struct spi_transfer *t,unsigned words,unsigned bits_per_word)
{
    bool use_descriptor;
    u32 cfg,div=120;
'''
        program += selection + '\nreturn cfg;\n}\n'
        program += r'''
int main(int argc,char **argv)
{
    struct apple_spi spi={&apple_spi_hw_t7000,0};
    struct spi_device device={0};
    struct spi_transfer transfer={0};
    unsigned mode,bits,index;
    static const unsigned counts[]={1,2,8,16,17,16384,32768};
    assert(argc==2);
    if(!strcmp(argv[1],"t7000")) {
        assert(spi.hw->legacy_status && !spi.hw->has_descriptor_dma);
        for(mode=0;mode<4;mode++) for(bits=8;bits<=16;bits+=8) {
            device.mode=mode;
            for(index=0;index<sizeof(counts)/sizeof(counts[0]);index++) {
                u32 expected=0x4018|(mode<<1)|(bits==16?0x8000:0);
                assert(select_config(&spi,&device,&transfer,counts[index],bits)==expected);
                assert(!prepared_descriptor && !descriptor_calls && !old_config_calls);
            }
        }
        device.mode=0;
        assert(apple_spi_t7000_config(&device,8)==0x4018);
        assert((apple_spi_t7000_config(&device,8)|0x20)==0x4038);
        assert(apple_spi_t7000_config(&device,16)==0xc018);
        return 0;
    }
    if(!strcmp(argv[1],"t8010")) {
        spi.hw=&apple_spi_hw_t8010;
        assert(spi.hw->legacy_status && spi.hw->has_descriptor_dma);
        assert(select_config(&spi,&device,&transfer,16,8)==0x401e);
        assert(prepared_descriptor && descriptor_calls==1 && old_config_calls==1);
        assert(select_config(&spi,&device,&transfer,17,8)==0x104018);
        assert(!prepared_descriptor && descriptor_calls==2 && old_config_calls==2);
        return 0;
    }
    return 2;
}
'''
        source = root / 'test.c'
        source.write_text(program)
        cls.binary = root / 'n61-spi-config'
        subprocess.run([compiler, '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        str(source), '-o', str(cls.binary)], check=True,
                       capture_output=True, text=True)
        cls.controller = controller

    def test_t7000_mode_word_size_and_every_length_use_its_native_fifo_config(self):
        subprocess.run([self.binary, 't7000'], check=True)

    def test_t8010_keeps_its_existing_descriptor_and_fifo_branches(self):
        subprocess.run([self.binary, 't8010'], check=True)

    def test_dma_allocation_remains_gated_to_the_descriptor_backend(self):
        self.assertIn('if (spi->hw->has_descriptor_dma) {\n\t\tctlr->dma_tx =', self.controller)


if __name__ == '__main__':
    unittest.main()

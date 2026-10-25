// SPDX-License-Identifier: GPL-2.0-or-later
/* One zero-only SPI DMA transfer. Reset is NEVER released. No firmware/input. */
#ifdef SPI_RESET_MODEL
#include "reset-test-kernel.h"
#else
#include <linux/atomic.h>
#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/property.h>
#include <linux/slab.h>
#include <linux/spi/spi.h>
#include "quiesce-api.h"
#endif

static atomic_t attempted=ATOMIC_INIT(0);
static int result=-EINPROGRESS;
module_param(result,int,0444);
MODULE_PARM_DESC(result,"0 only after reset-held SPI transfer and one actual DMA issue");
struct reset_check {
 struct spi_device *spi;
 struct gpio_desc *reset,*ana,*ldo;
 struct clk *clk;
 bool clock_on,ana_ready,ldo_ready;
};
static void stop(struct reset_check *test)
{
 gpiod_set_value_cansleep(test->reset,1);
 if(test->clock_on){clk_disable_unprepare(test->clk);test->clock_on=false;}
 if(test->ldo_ready)gpiod_set_value_cansleep(test->ldo,0);
 if(test->ana_ready)gpiod_set_value_cansleep(test->ana,0);
 dev_info(&test->spi->dev,"SPI_DMA_RESET_STOP reset=asserted touch-power=off\n");
}
static int reset_probe(struct spi_device *spi)
{
 struct device *dev=&spi->dev;
 struct reset_check *test;
 struct spi_transfer xfer={.len=229524,.bits_per_word=16,.speed_hz=4000000};
 unsigned int before,after;
 u8 *zeros;
 int ret;
 if(!of_machine_is_compatible("apple,j71b") ||
    !of_device_is_compatible(dev->of_node,"apple,j71b-touchscreen-readiness") ||
    !device_property_read_bool(dev,"apple,dma-reset-only") ||
    !device_property_read_bool(dev,"apple,readiness-only"))return -ENODEV;
 if(spi->max_speed_hz!=4000000 || (spi->mode&(SPI_CPHA|SPI_CPOL))!=SPI_MODE_3 ||
    !spi->controller->dma_tx || !spi->controller->can_dma || !apple_sio_payload_allowed())return -EINVAL;
 zeros=devm_kzalloc(dev,xfer.len,GFP_KERNEL);
 test=devm_kzalloc(dev,sizeof(*test),GFP_KERNEL);
 if(!test||!zeros)return -ENOMEM;
 xfer.tx_buf=zeros;
 if(!spi->controller->can_dma(spi->controller,spi,&xfer))return -EINVAL;
 test->spi=spi;
 test->clk=devm_clk_get(dev,NULL);
 if(IS_ERR(test->clk))return PTR_ERR(test->clk);
 test->reset=devm_gpiod_get(dev,"reset",GPIOD_ASIS);
 if(IS_ERR(test->reset))return PTR_ERR(test->reset);
 test->ana=devm_gpiod_get(dev,"power-ana",GPIOD_ASIS);
 if(IS_ERR(test->ana))return PTR_ERR(test->ana);
 test->ldo=devm_gpiod_get(dev,"power-ldo",GPIOD_ASIS);
 if(IS_ERR(test->ldo))return PTR_ERR(test->ldo);
 if(atomic_xchg(&attempted,1))return -EALREADY;
 ret=gpiod_direction_output(test->reset,1);
 if(ret)return ret;
 ret=gpiod_direction_output(test->ana,0);if(ret)goto out;
 test->ana_ready=true;
 ret=gpiod_direction_output(test->ldo,0);if(ret)goto out;
 test->ldo_ready=true;
 msleep(50);
 gpiod_set_value_cansleep(test->ana,1);usleep_range(10000,11000);
 gpiod_set_value_cansleep(test->ldo,1);usleep_range(1000,2000);
 ret=clk_prepare_enable(test->clk);if(ret)goto out;
 test->clock_on=true;usleep_range(15000,16000);
 before=apple_sio_payload_submits();
 dev_info(dev,"SPI_DMA_RESET_BEGIN bytes=229524 bpw=16 max-hz=4000000 reset=asserted\n");
 ret=spi_sync_transfer(spi,&xfer,1);
 after=apple_sio_payload_submits();
 dev_info(dev,"SPI_DMA_RESET_RETURN status=%d dma-issues-before=%u after=%u\n",ret,before,after);
 if(!ret && after-before!=1)ret=-EPROTO; /* PIO fallback is NOT a DMA PASS. */
 out:
 stop(test);
 result=ret;
 if(!ret)dev_info(dev,"SPI_DMA_RESET_PASS zero-bytes=229524 firmware-bytes=0 reset-never-released\n");
 else dev_err(dev,"SPI_DMA_RESET_FAIL status=%d no-retry\n",ret);
 return ret;
}
/* No matching OF table: only explicit standard driver_override selects this. */
static const struct spi_device_id reset_ids[]={{"j71b-dma-reset",0},{}};
MODULE_DEVICE_TABLE(spi,reset_ids);
static struct spi_driver reset_driver={
 .probe=reset_probe,
 .id_table=reset_ids,
 .driver={.name="j71b-dma-reset"},
};
module_spi_driver(reset_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J71b one-shot 229524-zero DMA check under reset; no firmware");

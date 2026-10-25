/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
typedef uint8_t u8;
typedef int atomic_t;
#define ATOMIC_INIT(v) (v)
static int atomic_xchg(atomic_t*p,int v){int old=*p;*p=v;return old;}
#define module_param(...)
#define MODULE_PARM_DESC(...)
#define MODULE_DEVICE_TABLE(...)
#define module_spi_driver(...)
#define MODULE_LICENSE(...)
#define MODULE_DESCRIPTION(...)
#define dev_info(...)
#define dev_err(...)
#define GFP_KERNEL 0
#define GPIOD_ASIS 0
#define SPI_CPOL 2
#define SPI_CPHA 1
#define SPI_MODE_3 3
#define EPROBE_DEFER 517
#define IS_ERR(p) ((intptr_t)(p)<0)
#define PTR_ERR(p) ((int)(intptr_t)(p))
struct device {void*of_node;};
struct gpio_desc {int value;bool output;};
struct clk {int unused;};
struct spi_device;
struct spi_transfer {const void*tx_buf;void*rx_buf;unsigned int len,bits_per_word,speed_hz;};
struct spi_controller {void*dma_tx;bool(*can_dma)(struct spi_controller*,struct spi_device*,struct spi_transfer*);};
struct spi_device {struct device dev;unsigned int max_speed_hz,mode;struct spi_controller*controller;};
struct spi_device_id {const char*name;int data;};
struct spi_driver {int(*probe)(struct spi_device*);const struct spi_device_id*id_table;struct{const char*name;}driver;};
static struct {bool board,node,marker,readiness,allow,dma;int fail;unsigned writes,transfers,issues;bool clock;struct gpio_desc reset,ana,ldo;struct clk clk;} model;
enum {OK,CLOCK_FAIL,TRANSFER_FAIL,PIO_FALLBACK,DEFER_CLOCK,ANA_DIRECTION_FAIL,LDO_DIRECTION_FAIL};
static bool of_machine_is_compatible(const char*n){(void)n;return model.board;}
static bool of_device_is_compatible(void*p,const char*n){(void)p;(void)n;return model.node;}
static bool device_property_read_bool(struct device*d,const char*n){(void)d;return !strcmp(n,"apple,dma-reset-only")?model.marker:model.readiness;}
static bool apple_sio_payload_allowed(void){return model.allow;}
static unsigned int apple_sio_payload_submits(void){return model.issues;}
static void*devm_kzalloc(struct device*d,size_t n,int f){(void)d;(void)f;return calloc(1,n);}
static struct clk*devm_clk_get(struct device*d,void*n){(void)d;(void)n;return model.fail==DEFER_CLOCK?(void*)(intptr_t)-EPROBE_DEFER:&model.clk;}
static struct gpio_desc*devm_gpiod_get(struct device*d,const char*n,int f){(void)d;assert(f==GPIOD_ASIS);if(!strcmp(n,"reset"))return &model.reset;if(!strcmp(n,"power-ana"))return &model.ana;assert(!strcmp(n,"power-ldo"));return &model.ldo;}
static int gpiod_direction_output(struct gpio_desc*g,int v){
 model.writes++;if(g==&model.reset)assert(v==1); /* NEVER release reset */
 if((g==&model.ana&&model.fail==ANA_DIRECTION_FAIL)||(g==&model.ldo&&model.fail==LDO_DIRECTION_FAIL))return -EIO;
 g->value=v;g->output=true;return 0;
}
static void gpiod_set_value_cansleep(struct gpio_desc*g,int v){assert(g->output);if(g==&model.reset)assert(v==1);model.writes++;g->value=v;}
static int clk_prepare_enable(struct clk*c){assert(c==&model.clk);if(model.fail==CLOCK_FAIL)return -EIO;model.clock=true;return 0;}
static void clk_disable_unprepare(struct clk*c){assert(c==&model.clk&&model.clock);model.clock=false;}
static void msleep(unsigned int n){assert(n<=50);}
static void usleep_range(unsigned int a,unsigned int b){assert(a<=b&&b<=16000);}
static bool can_dma(struct spi_controller*c,struct spi_device*s,struct spi_transfer*t){(void)c;(void)s;assert(t->len==229524&&t->bits_per_word==16&&!t->rx_buf);return model.dma;}
static int spi_sync_transfer(struct spi_device*s,struct spi_transfer*t,int n){
 (void)s;assert(n==1&&t->len==229524&&t->bits_per_word==16&&t->speed_hz==4000000&&!t->rx_buf);
 assert(model.reset.value==1&&model.ana.value==1&&model.ldo.value==1&&model.clock);
 for(unsigned int i=0;i<t->len;i++)assert(!((const u8*)t->tx_buf)[i]);
 model.transfers++;assert(model.transfers==1);
 if(model.fail!=PIO_FALLBACK)model.issues++;
 return model.fail==TRANSFER_FAIL?-EIO:0;
}

// SPDX-License-Identifier: GPL-2.0-only
/* Guarded J72 Z2 firmware upload, 12-byte execute and read-only device-info query. */
#include <linux/delay.h>
#include <linux/i2c.h>
#include <linux/io.h>
#include <linux/jiffies.h>
#include <linux/ktime.h>
#include <linux/module.h>
#include <linux/kobject.h>
#include <linux/sysfs.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/regmap.h>
#include <linux/spi/spi.h>
#include <linux/workqueue.h>
#include <linux/kthread.h>
#include <linux/wait.h>
#include <linux/mutex.h>
#include <linux/input.h>
#include <linux/input/mt.h>
#include "j72-classic-contacts.h"

#define AUDIT_CS_US 10U
#define AUDIT_SYNC 1U
#define AUDIT_STREAM 0U
#define AUDIT_POLICY 0U

#define PWM_PATH "/soc/pwm-observer@20a118000"
#define DOMAIN_PATH "/soc/power-management@20e000000/power-controller@20070"
#define PMIC_PATH "/soc/i2c@20a110000/pmic@3c"
#define SPI_PATH "/soc/spi@20a088000"
#define PWM_PHYS 0x20a118000ULL
#define GPIO_PHYS 0x20e300000ULL
#define PMGR_PHYS 0x20e020000ULL
#define PWM0_PSTATE 0x70
#define SPI2_PSTATE 0xa8
#define GPIO15_OFFSET (15 * 4)
#define GPIO79_OFFSET (79 * 4)
#define GPIO111_OFFSET (111 * 4)
#define COUNT_HALF 366U /* 24 MHz / 32768 Hz, nearest even period 732 */

static void __iomem *after_pmgr;
static bool attempted;
static bool cleanup_failed;
static bool cleanup_ok;
module_param(cleanup_ok,bool,0400);
static unsigned int session_state;
static DEFINE_MUTEX(session_control);
static const u8 dummy_frame[16] = {
	0x1a, 0xa1, 0x18, 0xe1, 0x18, 0xe1, 0x18, 0xe1,
	0x18, 0xe1, 0x18, 0xe1, 0x18, 0xe1, 0x18, 0xe1,
};

static const u8 fw_packet[] = {
#include "j72_fw_packet.inc"
};
/* Drain MISO during the one continuous firmware transaction. */
static u8 fw_rx[sizeof(fw_packet)];
static ssize_t fw_rx_read(struct file *file, struct kobject *kobj,
 const struct bin_attribute *attr, char *buf, loff_t off, size_t count)
{
 if (off < 0 || off >= sizeof(fw_rx)) return 0;
 count = min_t(size_t, count, sizeof(fw_rx) - off);
 memcpy(buf, fw_rx + off, count);
 return count;
}
static struct bin_attribute fw_rx_attr = {
 .attr = { .name = "j72_touch_session_fw_rx", .mode = 0400 },
 .size = sizeof(fw_rx), .read = fw_rx_read,
};


static bool hbpp_word_ok(u16 v)
{
	switch (v) {
	case 0x18e1: case 0x1aa1: case 0x1f01: case 0x4879:
	case 0x4969: case 0x4ad1: case 0x4bc1:
		return true;
	default:
		return false;
	}
}

static int match_pmic(struct device *dev, const void *data)
{
	struct i2c_client *client = i2c_verify_client(dev);
	return client && client->addr == 0x3c && dev->of_node == data &&
	       of_device_is_compatible(dev->of_node, "apple,anya-pmic");
}

static void after_detach(struct work_struct *unused)
{
	u32 state = readl(after_pmgr + PWM0_PSTATE);
	u32 spi2 = readl(after_pmgr + SPI2_PSTATE);
	pr_info("j72_touch_session: PMGR_PWM0_AFTER=%08x PMGR_SPI2_AFTER=%08x\n",
		state, spi2);
	if (state!=0x200 || spi2!=0x200)cleanup_failed=true;
	if (((state >> 4) & 0xf) != 0 || ((spi2 >> 4) & 0xf) != 0)
		pr_err("j72_touch_session: PWM0 or SPI2 domain remained active\n");
	iounmap(after_pmgr);
	after_pmgr = NULL;
}
static DECLARE_DELAYED_WORK(after_work, after_detach);

/* Exact N1 wake helper 0xfffffff007e9b3d4 sends EE then waits2ms.
 * Apply before E2 after idle interval. No controller register write.
 */

/* Matching Z2 virtual-call order: extra data, prox, MtCl, firmware,
 * performCalibSeq, execute. Only exact J72 private MtCl is present here.
 */
static const u8 prox_packet[]={
#include "j72_prox_packet.inc"
};
static const u8 cal_packet_0[]={
#include "j72_cal_packet_0.inc"
};
static const u8 cal_packet_1[]={
#include "j72_cal_packet_1.inc"
};
static u8 cal_rx[1024];
static noinline int checked_xfer(struct spi_device *dev,const u8 *tx,u8 *rx,unsigned int len)
{
 struct spi_transfer t={.tx_buf=tx,.rx_buf=rx,.len=len,.speed_hz=1000000,.bits_per_word=8,
  .word_delay={.value=5,.unit=SPI_DELAY_UNIT_USECS}};
 struct spi_message m;int ret;
 spi_message_init(&m);spi_message_add_tail(&t,&m);ret=spi_sync(dev,&m);
 if(ret || m.actual_length!=len){pr_err("j72_touch_session: PROTOCOL_XFER ret=%d actual=%u expected=%u\n",ret,m.actual_length,len);return ret?ret:-EIO;}
 return 0;
}
static noinline bool upload_calibration(struct spi_device *dev)
{
 unsigned int i;
 const u8 *packets[]={prox_packet,cal_packet_0,cal_packet_1};
 const unsigned int lengths[]={sizeof(prox_packet),sizeof(cal_packet_0),sizeof(cal_packet_1)};
 u8 ack_tx[2]={0x1a,0xa1},ack_rx[2]={0};
 for(i=0;i<3;i++){
  int ret=checked_xfer(dev,packets[i],cal_rx,lengths[i]);
  if(ret)return false;
  ret=checked_xfer(dev,ack_tx,ack_rx,2);
  pr_info("j72_touch_session: CAL_PACKET=%u len=%u ACK=%02x%02x accepted=%d\n",i,lengths[i],ack_rx[0],ack_rx[1],!ret && ack_rx[0]==0x4b && ack_rx[1]==0xc1);
  if(ret || ack_rx[0]!=0x4b || ack_rx[1]!=0xc1)return false;
 }
 return true;
}
static void encode_word(u8 *p,u32 v)
{p[0]=v>>8;p[1]=v;p[2]=v>>24;p[3]=v>>16;}
static noinline bool read_register(struct spi_device *dev,u32 addr,u32 *value);
static noinline bool perform_calibration(struct spi_device *dev)
{
 /* Exact N1 override0xfffffff007e9c310, defaults0xfffffff007e9c584,
  * live J72 clock-enable=1; C1F15 ID uses const-cal default3.
  */
 const u32 reg[][3]={{0x10003060,6089,0xffffffff},{0x1000305c,0x20,0xffffffff},
  {0x10003058,6,0xffffffff},{0x10003000,3,0xffffffff},{0x10003518,1,0xffffffff}};
 unsigned int i,j;
 for(i=0;i<ARRAY_SIZE(reg);i++){
  u8 tx[16]={0x1e,0x33},rx[16]={0},ack_tx[2]={0x1a,0xa1},ack_rx[2]={0};u16 sum=0;int ret;
  encode_word(tx+2,reg[i][0]);encode_word(tx+6,reg[i][2]);encode_word(tx+10,reg[i][1]);
  for(j=2;j<14;j++) { sum+=tx[j]; }
  tx[14]=sum>>8;tx[15]=sum;
  ret=checked_xfer(dev,tx,rx,16);if(ret)return false;
  ret=checked_xfer(dev,ack_tx,ack_rx,2);
  pr_info("j72_touch_session: CAL_REG=%08x ACK=%02x%02x accepted=%d\n",reg[i][0],ack_rx[0],ack_rx[1],!ret && ack_rx[0]==0x4a && ack_rx[1]==0xd1);
  if(ret || ack_rx[0]!=0x4a || ack_rx[1]!=0xd1)return false;
 }
 {
  u8 tx[2]={0x1f,1},rx[2]={0},ack_tx[2]={0x1a,0xa1},ack_rx[2]={0};int ret;
  {u32 apu;if(!read_register(dev,0x10003800,&apu))return false;
   pr_info("j72_touch_session: SPI_APU_EN=%08x\n",apu);}
  ret=checked_xfer(dev,tx,rx,2);if(ret)return false;
  pr_info("j72_touch_session: CAL_REQUEST reply=%02x%02x\n",rx[0],rx[1]);
  msleep(65);
  ret=checked_xfer(dev,ack_tx,ack_rx,2);
  pr_info("j72_touch_session: CAL_DONE ret=%d status=%02x%02x\n",ret,ack_rx[0],ack_rx[1]);
  /* Apple method gates on successful ACK transport; status interpretation
   * is observed here, not assigned an invented success constant.
   */
  return !ret;
 }
}

static noinline bool read_register(struct spi_device *dev,u32 addr,u32 *value)
{
 u8 tx[8]={0x1c,0x73},rx[8]={0};
 u8 ack_tx[8]={0x1a,0xa1,0x18,0xe1,0x18,0xe1,0x18,0xe1},ack_rx[8]={0};
 u16 sum=0,received;unsigned int i;int ret;
 encode_word(tx+2,addr);for(i=2;i<6;i++)sum+=tx[i];tx[6]=sum>>8;tx[7]=sum;
 ret=checked_xfer(dev,tx,rx,8);if(ret)return false;
 ret=checked_xfer(dev,ack_tx,ack_rx,8);if(ret)return false;
 sum=0;for(i=2;i<6;i++)sum+=ack_rx[i];received=((u16)ack_rx[6]<<8)|ack_rx[7];
 *value=((u32)ack_rx[4]<<24)|((u32)ack_rx[5]<<16)|((u32)ack_rx[2]<<8)|ack_rx[3];
 pr_info("j72_touch_session: REG_READ=%08x value=%08x valid=%d status=%02x%02x checksum=%04x received=%04x\n",addr,*value,ack_rx[0]==0x4c && ack_rx[1]==0x39 && sum==received,ack_rx[0],ack_rx[1],sum,received);
 return ack_rx[0]==0x4c && ack_rx[1]==0x39 && sum==received;
}
static noinline bool read_chip_version(struct spi_device *dev)
{
 u32 value;
 return read_register(dev,0x10008ffc,&value) && value==0x434d15c1;
}

static noinline bool read_device_info(struct spi_device *spi_dev)
{
 int ret;
 {
  u8 info_tx[16] = {0xe2}, info_rx[2][16] = {{0}};
  u16 checksum = 0, received;
  unsigned int stage, j;
  struct spi_transfer info_xfer = {
   .len=16,.speed_hz=1000000,.bits_per_word=8,
   .word_delay={.value=5,.unit=SPI_DELAY_UNIT_USECS},
  };
  struct spi_message info_msg;
  {
   u8 wake_tx[16]={0xee},wake_rx[16]={0};
   struct spi_transfer t={.tx_buf=wake_tx,.rx_buf=wake_rx,.len=16,
    .speed_hz=1000000,.bits_per_word=8,
    .word_delay={.value=5,.unit=SPI_DELAY_UNIT_USECS}};
   struct spi_message m;
   wake_tx[14]=0xee;
   spi_message_init(&m);spi_message_add_tail(&t,&m);
   ret=spi_sync(spi_dev,&m);
   pr_info("j72_touch_session: APP_WAKE ret=%d actual=%u reply=%*phN\n",ret,m.actual_length,16,wake_rx);
   if(ret || m.actual_length!=16) return false;
   msleep(2);
  }
  info_tx[14] = 0xe2;
  for (stage=0; stage<2; stage++) {
   spi_message_init(&info_msg);
   info_xfer.tx_buf=info_tx;info_xfer.rx_buf=info_rx[stage];
   spi_message_add_tail(&info_xfer,&info_msg);
   ret=spi_sync(spi_dev,&info_msg);
   pr_info("j72_touch_session: INFO_STAGE=%u ret=%d actual=%u reply=%*phN\n",
    stage+1,ret,info_msg.actual_length,16,info_rx[stage]);
   if(ret || info_msg.actual_length!=16) return false;
  }
  for(j=0;j<14;j++) checksum+=info_rx[1][j];
  received=info_rx[1][14]|((u16)info_rx[1][15]<<8);
  pr_info("j72_touch_session: INFO_VALID=%d checksum=%04x received=%04x version=%u max_packet=%u\n",
   info_rx[1][0]==0xe2 && checksum==received,checksum,received,
   info_rx[1][2],info_rx[1][3]|((u16)info_rx[1][4]<<8));
  return info_rx[1][0]==0xe2 && checksum==received;
 }
}


static bool valid_app_reply(const u8 *rx)
{
 u16 sum=0;unsigned int i;
 for(i=0;i<14;i++)sum+=rx[i];
 return sum==(rx[14]|((u16)rx[15]<<8));
}
static u8 report_data_tx[2464],report_data_rx[2464];

static int app_xfer(struct spi_device *dev,u8 *tx,u8 *rx)
{u16 sum=0;unsigned int i;for(i=0;i<14;i++)sum+=tx[i];tx[14]=sum;tx[15]=sum>>8;return checked_xfer(dev,tx,rx,16);}
/* N1 framing from Orchard PR28 input/0009, pinned25bfbd4d53a6d5acb680711bd0219d1510839637.
 * E6 phase1 repeats E6 with bit1 set, carrying phase0 checksum unchanged.
 * This comparison changes control reads only; EB data requests are unchanged.
 */
static bool short_report(struct spi_device *dev,u8 id,unsigned int len,u8 *data)
{
 unsigned int attempt;
 if(!len || len>11)return false;
 for(attempt=0;attempt<3;attempt++){
  u8 tx[16]={0xe6},rx[16]={0};bool valid;
  tx[1]=id;tx[3]=len;
  msleep(2);
  if(app_xfer(dev,tx,rx))return false; /* Do not retry transport errors. */
  udelay(25);tx[2]=1;
  /* Repeat phase0 checksum unchanged in phase1, as before. */
  if(checked_xfer(dev,tx,rx,16))return false;
  valid=rx[0]==0xe6 && rx[1]==id && rx[2]==0 && valid_app_reply(rx);
  pr_info("j72_touch_session: SHORT_REPORT=%02x attempt=%u valid=%d raw=%*phN\n",id,attempt+1,valid,16,rx);
  if(valid){memcpy(data,rx+3,len);return true;}
  /* Exact observed failure: stale E1 reply to readonly C8. Restart both
   * phases at most twice; never accept stale bytes or relax health/gate.
   */
 }
 return false;
}
/* Targeted read-only getters audited locally: AC loads one byte from
 * RAM state base; AB loads one byte from status state. No unknown ID sweep,
 * metadata query, firmware-memory access command, or new report write. */
/* Exact targeted DE getter locally audited: copies40bytes from the
 * processing-counter structure, without hardware access or state changes. */
static bool processing_stats(struct spi_device *dev,unsigned int stage)
{
 u8 tx[45]={0xe7,0xde,0,40},rx[45]={0};u16 sum=0,got;unsigned int i;
 if(app_xfer(dev,tx,rx))return false;
 udelay(25);
 memset(tx,0,sizeof(tx));tx[0]=0xe7;tx[1]=0xde;tx[2]=1;tx[3]=40;
 for(i=0;i<43;i++){if(i!=2)sum+=tx[i];} /* phase0 checksum */
 tx[43]=sum;tx[44]=sum>>8;
 if(checked_xfer(dev,tx,rx,45))return false;
 sum=0;for(i=0;i<43;i++)sum+=rx[i];got=rx[43]|((u16)rx[44]<<8);
 if(rx[0]!=0xe7 || rx[1]!=0xde || rx[2] || sum!=got)return false;
 for(i=0;i<10;i++) {
  u8 *v=rx+3+4*i;u32 value=v[0]|((u32)v[1]<<8)|((u32)v[2]<<16)|((u32)v[3]<<24);
  pr_info("j72_touch_session: PROCESS_STATS stage=%u offset=%02x value=%u\n",stage,4*i,value);
 }
 return true;
}
/* Exact C8 getter7242->72c2 reads active report mask403d60.
 * Setter C8:7952->7a34->7af4 checks1byte and only stores403d60.
 * Gate83f6 marks0x01 for contacts and0x10 for periodic/empty results;
 * preserve default0x08 event bit and request0x19 as a positive control. */
static bool enable_native_results(struct spi_device *dev)
{
 u8 tx[16]={0xe4,0xc8,1,0x19},rx[16]={0},mask,mode;
 if(!short_report(dev,0xac,1,&mode) || mode!=2 || !short_report(dev,0xc8,1,&mask) || mask!=8)return false;
 if(app_xfer(dev,tx,rx))return false;
 msleep(2);memset(tx,0,16);tx[0]=0xe1;
 if(app_xfer(dev,tx,rx) || rx[0]!=0xe1 || rx[6]!=0xe4 || rx[7]!=0xc8 || rx[8] || !valid_app_reply(rx))return false;
 msleep(20);
 if(!short_report(dev,0xc8,1,&mask) || mask!=0x19)return false;
 pr_info("j72_touch_session: NATIVE_GATE=19 verified\n");return true;
}
static bool state_reads(struct spi_device *dev,unsigned int stage)
{
 static const u8 ids[]={0xac,0xab,0xaf};
 unsigned int i;u8 value,flags[4],mask;
 for(i=0;i<ARRAY_SIZE(ids);i++) {
  if(!short_report(dev,ids[i],1,&value)) {
   pr_info("j72_touch_session: STATE_READ_FAILED stage=%u id=%02x\n",stage,ids[i]);return false;
  }
  pr_info("j72_touch_session: STATE_READ stage=%u id=%02x value=%02x\n",stage,ids[i],value);
 }
 if(!short_report(dev,0x7f,4,flags))return false;
 pr_info("j72_touch_session: CURRENT_FLAGS stage=%u value=%*phN\n",stage,4,flags);
 if(memchr_inv(flags,0,4))return false;
 if(!processing_stats(dev,stage))return false;
 if(!short_report(dev,0xc8,1,&mask) || mask!=0x19)return false;
 pr_info("j72_touch_session: NATIVE_GATE_STAGE=%u value=%02x\n",stage,mask);
 return true;
}
/* Audited AC setter only selects one of two RAM report-generator paths.
 * Default2 includes0x44 finger-result generation; value1 selects the path
 * preparing0x43 raw results. No firmware flash or new hardware-register write.
 * This is a diagnostic comparison, not a claim that raw mode is iOS default. */
static bool __maybe_unused select_raw_reports(struct spi_device *dev)
{
 u8 tx[16]={0xe4,0xac,1,1},rx[16]={0},mode,descriptor[8];
 if(!short_report(dev,0xac,1,&mode) || mode!=2)return false;
 if(app_xfer(dev,tx,rx))return false;
 msleep(2);memset(tx,0,16);tx[0]=0xe1;
 if(app_xfer(dev,tx,rx) || rx[0]!=0xe1 || rx[6]!=0xe4 || rx[7]!=0xac || rx[8] || !valid_app_reply(rx))return false;
 msleep(20);
 if(!short_report(dev,0xac,1,&mode) || mode!=1)return false;
 if(!short_report(dev,0xd0,8,descriptor))return false;
 pr_info("j72_touch_session: RAW_DESCRIPTOR=%*phN\n",8,descriptor);
 pr_info("j72_touch_session: RAW_MODE_CONFIRMED=1\n");return true;
}
static noinline bool setup_reports(struct spi_device *dev)
{
 /* N1 long-read and E4 short-write framing also match pinned Corellium
  * hx-touchd03db9c6. D9 is readonly; AF=0 requests normal operation.
  * Unsupported9d is deliberately not written.
  */
 u8 tx[32]={0xe7,0xd9,0,16},rx[32]={0},af,bf[4];u16 sum=0,received;unsigned int i;
 if(app_xfer(dev,tx,rx))return false;
 udelay(25);
 memset(tx,0,sizeof(tx));tx[0]=0xe7;tx[1]=0xd9;tx[2]=1;tx[3]=16;
 for(i=0;i<19;i++) { if(i!=2)sum+=tx[i]; } /* phase0 checksum */
 tx[19]=sum;tx[20]=sum>>8;
 if(checked_xfer(dev,tx,rx,21))return false;
 sum=0;for(i=0;i<19;i++)sum+=rx[i];received=rx[19]|((u16)rx[20]<<8);
 pr_info("j72_touch_session: METRICS_VALID=%d raw=%*phN\n",rx[0]==0xe7 && rx[1]==0xd9 && !rx[2] && sum==received,21,rx);
 if(rx[0]!=0xe7 || rx[1]!=0xd9 || rx[2] || sum!=received)return false;
 if((s16)(rx[11]|rx[12]<<8)!=-112 || (s16)(rx[13]|rx[14]<<8)!=-112 || (s16)(rx[15]|rx[16]<<8)!=14508 || (s16)(rx[17]|rx[18]<<8)!=19426)return false;
 if(!short_report(dev,0xaf,1,&af) || !short_report(dev,0xbf,4,bf))return false;
 pr_info("j72_touch_session: MODE_BEFORE=%u FORMAT=%*phN\n",af,4,bf);
 memset(tx,0,16);tx[0]=0xe4;tx[1]=0xaf;tx[2]=1;
 if(app_xfer(dev,tx,rx))return false;
 memset(tx,0,16);tx[0]=0xe1;
 if(app_xfer(dev,tx,rx) || rx[0]!=0xe1 || rx[6]!=0xe4 || rx[7]!=0xaf || rx[8] || !valid_app_reply(rx))return false;
 if(!short_report(dev,0xaf,1,&af) || af!=0)return false;
 pr_info("j72_touch_session: MODE_AFTER=0 confirmed\n");return true;
}

/* Exact iOS command-status validation uses E1 header, checksum, and
 * command/report/status at bytes6/7/8 (methods 7e98678/7e987e0). */
static bool __maybe_unused stream_setup(struct spi_device *dev)
{
 u8 tx[16]={0xe4,0xbf,4,0x95,0x81,0x09,0},rx[16]={0};
 u8 bf[4],af;unsigned int i;
 if(app_xfer(dev,tx,rx))return false;
 msleep(2);
 memset(tx,0,16);tx[0]=0xe1;
 if(app_xfer(dev,tx,rx))return false;
 pr_info("j72_touch_session: BF_WRITE_ACK=%*phN\n",16,rx);
 if(rx[0]!=0xe1 || rx[6]!=0xe4 || rx[7]!=0xbf || rx[8] || !valid_app_reply(rx))return false;
 /* A matching write reply alone is insufficient: wake and read back
  * the requested value across multiple independent delayed reads.
  */
 for(i=0;i<3;i++) {
  msleep(20);
  if(!read_device_info(dev))return false;
  if(!short_report(dev,0xbf,4,bf))return false;
  pr_info("j72_touch_session: BF_SETTLED=%u value=%*phN accepted=%d\n",i,4,bf,!memcmp(bf,"\x95\x81\x09\x00",4));
  if(memcmp(bf,"\x95\x81\x09\x00",4))return false;
 }
 memset(tx,0,16);tx[0]=0xe4;tx[1]=0xaf;tx[2]=1;
 if(app_xfer(dev,tx,rx))return false;
 msleep(2);memset(tx,0,16);tx[0]=0xe1;
 if(app_xfer(dev,tx,rx))return false;
 pr_info("j72_touch_session: AF_WRITE_ACK=%*phN\n",16,rx);
 if(rx[0]!=0xe1 || rx[6]!=0xe4 || rx[7]!=0xaf || rx[8] || !valid_app_reply(rx))return false;
 msleep(20);
 return read_device_info(dev) && short_report(dev,0xaf,1,&af) && af==0;
}

/* Raw reply audit: silence is not a valid zero-length protocol reply.
 * Fixed-size private snapshot; firmware, calibration and captures stay local.
 * Policy 0 flips EB tag only after a valid complete read, matching the iOS
 * readOneFrameOfData success path. Policy 1 preserves the earlier always-flip
 * comparison. Neither policy treats an all-zero reply as protocol success.
 */
#define AUDIT_CAPACITY 64
#define AUDIT_DATA_BYTES 2464
#define AUDIT_ZERO       0x0001
#define AUDIT_PADDING    0x0002
#define AUDIT_LENGTH_OK  0x0004
#define AUDIT_POSITIVE   0x0008
#define AUDIT_STALE      0x0010
#define AUDIT_BAD_SUM    0x0020
#define AUDIT_BAD_HEADER 0x0040
#define AUDIT_BAD_LENGTH 0x0080
#define AUDIT_FRAME_OK   0x0100
#define AUDIT_FRAME_BAD  0x0200
#define AUDIT_PRIME      0x0400
#define AUDIT_ATTN_HIGH  0x1000
#define AUDIT_ATTN_LOW   0x2000

struct audit_record {
 u32 ms;
 u16 kind, length; /* 1=length request, 2=data transaction */
 u32 flags;
 u16 stored, reserved;
 u8 tx[16], rx[AUDIT_DATA_BYTES];
};
struct audit_snapshot {
 u32 magic, version, done, records;
 u32 cs_us, sync_enable, stream_enable, policy, seconds;
 u32 loops, zeros, padding, length_zero, length_positive, stale;
 u32 bad_header, bad_sum, bad_length, data_zero, frames, bad_frame;
 u32 xfer_errors, info_end, attention_high, attention_low, dropped;
 struct audit_record rec[AUDIT_CAPACITY];
};
static_assert(sizeof(struct audit_record)==2496);
static_assert(sizeof(struct audit_snapshot)==159848);
static struct audit_snapshot audit;
module_param_named(xfer_errors,audit.xfer_errors,uint,0400);
module_param_named(firmware_healthy,audit.info_end,uint,0400);
static unsigned int capture_seconds = 1800;
module_param(capture_seconds,uint,0400);
static bool stop_requested;
module_param(stop_requested,bool,0600);

static ssize_t audit_read(struct file *file,struct kobject *kobj,
 const struct bin_attribute *attr,char *buf,loff_t off,size_t count)
{
 ssize_t ret;
 mutex_lock(&session_control);
 if(session_state!=0 && session_state!=4){ret=-EBUSY;goto out;}
 if(off<0 || off>=sizeof(audit)){ret=0;goto out;}
 count=min_t(size_t,count,sizeof(audit)-off);
 memcpy(buf,(u8 *)&audit+off,count);ret=count;
out:mutex_unlock(&session_control);return ret;
}
static struct bin_attribute audit_attr = {
 .attr={.name="j72_touch_session_anomalies",.mode=0400},
 .size=sizeof(audit),.read=audit_read,
};

/* Two independent private anomaly rings:32 layout rejects,32 wire rejects.
 * Good frames are counted and emitted, not retained. Record counters grow
 * monotonically; each half overwrites its own oldest entry. Export when idle.
 */
static unsigned int layout_anomalies,wire_anomalies;
static void audit_save(u64 start,u16 kind,const u8 *tx,const u8 *rx,
 unsigned int length,u32 flags)
{
 struct audit_record *r;unsigned int slot;
 if(flags&0x0800)slot=(layout_anomalies++)%32;
 else if((kind==2 && (flags&AUDIT_FRAME_BAD)) ||
         (kind==1 && (flags&(AUDIT_STALE|AUDIT_BAD_SUM|AUDIT_BAD_HEADER|AUDIT_BAD_LENGTH))))
  slot=32+(wire_anomalies++)%32;
 else return;
 audit.records=layout_anomalies+wire_anomalies;
 r=&audit.rec[slot];memset(r,0,sizeof(*r));
 r->ms=div_u64(ktime_get_ns()-start,1000000);r->kind=kind;r->length=length;
 r->stored=min(length,(unsigned int)AUDIT_DATA_BYTES);r->flags=flags;
 memcpy(r->tx,tx,min(length,16U));memcpy(r->rx,rx,r->stored);
}

static bool audit_data_reply(const u8 *rx,unsigned int wire)
{
 unsigned int inner,j;u16 sum=0,received;
 if(wire<7 || (rx[0]!=0xea && rx[0]!=0xeb))return false;
 for(j=0;j<5;j++)sum+=rx[j];
 if(sum&255)return false;
 inner=rx[2]|((u16)rx[3]<<8);
 /* Exact advertised size; padded or truncated payloads need investigation. */
 if(inner<2 || inner+5!=wire)return false;
 sum=0;for(j=5;j<inner+3;j++)sum+=rx[j];
 received=rx[wire-2]|((u16)rx[wire-1]<<8);
 return sum==received;
}

static DECLARE_WAIT_QUEUE_HEAD(session_wait);
static struct task_struct *session_thread;
static bool enabled;
static unsigned int session_number;
/* state:0idle,1starting,2inputready,3stopping,4failed. */
static int enabled_set(const char *val,const struct kernel_param *kp)
{
 bool next;int ret=kstrtobool(val,&next);if(ret)return ret;
 mutex_lock(&session_control);
 if(next && session_state && !enabled){ret=-EBUSY;goto out;}
 if(next && session_state==4){ret=-EIO;goto out;}
 WRITE_ONCE(enabled,next);
 if(!next)WRITE_ONCE(stop_requested,true);
 wake_up_interruptible(&session_wait);
out:mutex_unlock(&session_control);return ret;
}
static const struct kernel_param_ops enabled_ops={.set=enabled_set,.get=param_get_bool};
module_param_cb(enabled,&enabled_ops,&enabled,0600);
module_param(session_state,uint,0400);
module_param(session_number,uint,0400);
module_param(layout_anomalies,uint,0400);
module_param(wire_anomalies,uint,0400);
static struct input_dev *touch_input;
static unsigned long last_input_frame;
static unsigned int input_frames, input_bad_layouts, input_timeouts;
module_param(input_frames,uint,0400);
module_param(input_bad_layouts,uint,0400);
module_param(input_timeouts,uint,0400);
static bool input_active;
static unsigned int input_recoveries;
module_param(input_recoveries,uint,0400);
static int register_touch_input(void)
{
 int ret;
 touch_input=input_allocate_device();
 if(!touch_input)return -ENOMEM;
 touch_input->name="J72 native touchscreen";
 touch_input->phys="j72/spi2";
 touch_input->id.bustype=BUS_SPI;
 __set_bit(INPUT_PROP_DIRECT,touch_input->propbit);
 /* Exact J72 D9 bounds: left/top=-112, right=14508,bottom=19426.
  * Native Y grows upward; report screen Y downward. Geometry is guarded below.
  */
 input_set_abs_params(touch_input,ABS_MT_POSITION_X,0,14620,0,0);
 input_set_abs_params(touch_input,ABS_MT_POSITION_Y,0,19538,0,0);
 input_abs_set_res(touch_input,ABS_MT_POSITION_X,99);
 input_abs_set_res(touch_input,ABS_MT_POSITION_Y,99);
 ret=input_mt_init_slots(touch_input,J72_CONTACT_MAX,INPUT_MT_DIRECT|INPUT_MT_DROP_UNUSED);
 if(!ret)ret=input_register_device(touch_input);
 if(ret){input_free_device(touch_input);touch_input=NULL;return ret;}
 last_input_frame=jiffies;
 pr_info("j72_touch_session: INPUT_READY x=0..14620 y=0..19538 inverted_y=1 slots=10\n");
 return 0;
}
static void release_touch_input(void)
{
 if(!touch_input)return;
 input_mt_sync_frame(touch_input);input_sync(touch_input);
 input_active=false;
}
static bool emit_touch_input(const u8 *body,unsigned int size)
{
 struct j72_contact contacts[J72_CONTACT_MAX];unsigned int count,i;int slot;
 if(j72_classic_contacts(body,size,contacts,&count)){input_bad_layouts++;return false;}
 /* Parser validates the WHOLE report before any event can be emitted. */
 input_active=false;
 for(i=0;i<count;i++){
  struct j72_contact *c=&contacts[i];bool active=c->state==3||c->state==4;
  slot=input_mt_get_slot_by_key(touch_input,c->id);
  if(slot<0)continue;
  input_mt_slot(touch_input,slot);
  input_mt_report_slot_state(touch_input,MT_TOOL_FINGER,active);
  if(active){
   input_report_abs(touch_input,ABS_MT_POSITION_X,clamp(c->x+112,0,14620));
   input_report_abs(touch_input,ABS_MT_POSITION_Y,clamp(19426-c->y,0,19538));
   input_active=true;
  }
 }
 input_mt_sync_frame(touch_input);input_sync(touch_input);
 last_input_frame=jiffies;input_frames++;return true;
}

static noinline void query_report_metadata(struct spi_device *dev,void __iomem *gpio)
{
 u64 start,last_wake=0;unsigned long end;
 unsigned int seq=1,consecutive_bad=0;bool stopped=false,previous_success=false;
 audit.cs_us=AUDIT_CS_US;audit.sync_enable=AUDIT_SYNC;
 audit.stream_enable=AUDIT_STREAM;audit.policy=AUDIT_POLICY;
 capture_seconds=clamp(capture_seconds,1U,1800U);audit.seconds=capture_seconds;
 if(!setup_reports(dev) || !enable_native_results(dev) || !state_reads(dev,0)){
  audit.done=2;pr_info("j72_touch_session: AUDIT_SETUP_FAILED\n");return;
 }
 if(register_touch_input()){audit.done=2;return;}
 WRITE_ONCE(session_state,2);
 start=ktime_get_ns();end=jiffies+msecs_to_jiffies(capture_seconds*1000);
 pr_info("j72_touch_session: AUDIT_READY seconds=%u cs_us=%u sync=%u stream=%u policy=%u\n",
  capture_seconds,AUDIT_CS_US,AUDIT_SYNC,AUDIT_STREAM,AUDIT_POLICY);
 while(time_before(jiffies,end) && !READ_ONCE(stop_requested)) {
  bool prime=AUDIT_POLICY==2 && !previous_success;
read_again: ;
  u8 tx[16]={0xeb},rx[16]={0};unsigned int length,wire,j;
  u16 sum;bool success=false;u32 level,flags;
  u64 now=ktime_get_ns();
  level=readl(gpio+GPIO111_OFFSET)&1;flags=level?AUDIT_ATTN_HIGH:AUDIT_ATTN_LOW;
  if(prime)flags|=AUDIT_PRIME;
  audit.loops++;if(level)audit.attention_high++;else audit.attention_low++;
  if(now-last_wake>100000000ULL){
   u8 wake[16]={0xee};
   if(app_xfer(dev,wake,rx)){audit.xfer_errors++;stopped=true;break;}
   msleep(2);last_wake=ktime_get_ns();
  }
  tx[1]=seq;
  if(app_xfer(dev,tx,rx)){audit.xfer_errors++;stopped=true;break;}
  if(!memchr_inv(rx,0,16)) {audit.zeros++;flags|=AUDIT_ZERO;goto length_done;}
  if(!memchr_inv(rx,0,14)) {audit.padding++;flags|=AUDIT_PADDING;goto length_done;}
  if((rx[0]&0xf0)!=0xe0) {audit.bad_header++;flags|=AUDIT_BAD_HEADER;goto bad_length;}
  if(!valid_app_reply(rx)) {audit.bad_sum++;flags|=AUDIT_BAD_SUM;goto bad_length;}
  /* The matching iOS method accepts the E0 family. Preserve other valid
   * replies as stale here; don't clock a data frame using unrelated fields.
   */
  if(rx[0]!=0xe1) {audit.stale++;flags|=AUDIT_STALE;goto length_done;}
  length=rx[1]|((u16)rx[2]<<8);
  if(length>2454 || (length && length<2)) {audit.bad_length++;flags|=AUDIT_BAD_LENGTH;goto bad_length;}
  flags|=AUDIT_LENGTH_OK;
  if(!length){audit.length_zero++;success=true;consecutive_bad=0;goto length_done;}
  audit.length_positive++;flags|=AUDIT_POSITIVE;
  /* Matching iOS readOneFrameOfData repeats GetResultLength after an
   * earlier failed read (and on the first read). The priming length is
   * checked but discarded; both transactions use the same tag.
   */
  if(prime){success=true;consecutive_bad=0;goto length_done;}
  audit_save(start,1,tx,rx,16,flags);
  wire=length+5;
  memset(report_data_tx,0,sizeof(report_data_tx));
  report_data_tx[0]=0xeb;report_data_tx[1]=seq;report_data_tx[2]=1;
  sum=0;for(j=0;j<14;j++)sum+=report_data_tx[j];
  report_data_tx[wire-2]=sum;report_data_tx[wire-1]=sum>>8;
  if(checked_xfer(dev,report_data_tx,report_data_rx,wire)){
   audit.xfer_errors++;stopped=true;break;
  }
  flags=level?AUDIT_ATTN_HIGH:AUDIT_ATTN_LOW;
  if(!memchr_inv(report_data_rx,0,wire-2)) {
   audit.data_zero++;flags|=AUDIT_ZERO|AUDIT_FRAME_BAD;consecutive_bad++;
  } else if(audit_data_reply(report_data_rx,wire)) {
   audit.frames++;flags|=AUDIT_FRAME_OK;success=true;consecutive_bad=0;
   if(!emit_touch_input(report_data_rx+5,wire-7))flags|=0x0800;
  } else {audit.bad_frame++;flags|=AUDIT_FRAME_BAD;consecutive_bad++;}
  audit_save(start,2,report_data_tx,report_data_rx,wire,flags);
  goto next;
bad_length:
  consecutive_bad++;
length_done:
  audit_save(start,1,tx,rx,16,flags);
next:
  /* Do not consume another queue tag after a silent/invalid read. */
  if(prime && success){prime=false;goto read_again;}
  previous_success=success;
  if(AUDIT_POLICY==1 || success)seq=seq==1?2:1;
  if(consecutive_bad>=3){
   u8 status[4],gate;
   release_touch_input();
   if(++input_recoveries>6 || !read_device_info(dev) ||
      !short_report(dev,0x7f,4,status) || memchr_inv(status,0,4) ||
      !short_report(dev,0xc8,1,&gate) || gate!=0x19){stopped=true;break;}
   pr_info("j72_touch_session: INPUT_RECOVERY count=%u health=1 flags=0 gate=19\n",input_recoveries);
   consecutive_bad=0;previous_success=false;
  }
  if(input_active && time_after(jiffies,last_input_frame+msecs_to_jiffies(250))){release_touch_input();input_timeouts++;}
  msleep(25);
 }
 WRITE_ONCE(session_state,3);
 release_touch_input();input_unregister_device(touch_input);touch_input=NULL;
 pr_info("j72_touch_session: INPUT_DONE frames=%u bad_layouts=%u timeouts=%u\n",input_frames,input_bad_layouts,input_timeouts);
 if(!state_reads(dev,1))stopped=true;
 audit.info_end=read_device_info(dev);
 audit.done=stopped?3:1;
 pr_info("j72_touch_session: AUDIT_DONE done=%u loops=%u zero=%u padding=%u valid_empty=%u positive=%u stale=%u bad_header=%u bad_sum=%u bad_length=%u data_zero=%u frames=%u bad_frame=%u xfer_errors=%u info_end=%u high=%u low=%u dropped=%u\n",
  audit.done,audit.loops,audit.zeros,audit.padding,audit.length_zero,
  audit.length_positive,audit.stale,audit.bad_header,audit.bad_sum,
  audit.bad_length,audit.data_zero,audit.frames,audit.bad_frame,
  audit.xfer_errors,audit.info_end,audit.attention_high,audit.attention_low,audit.dropped);
}

static int pulse_probe(struct platform_device *pdev)
{
	struct device_node *expected, *domain, *expected_domain, *pmic_node, *spi_node;
	struct device *pmic_dev = NULL;
	struct spi_controller *ctlr = NULL;
	struct spi_device *spi_dev = NULL;
	struct spi_board_info board = {
		.modalias = "j72-touch-dummy-test",
		.max_speed_hz = 1000000,
		.chip_select = 0,
		.mode = SPI_MODE_0,
	};
	struct spi_transfer xfer = {
		.tx_buf = dummy_frame,
		.len = sizeof(dummy_frame),
		.speed_hz = 1000000,
		.bits_per_word = 8,
		.word_delay = { .value = 5, .unit = SPI_DELAY_UNIT_USECS },
	};
	struct spi_message msg, wake_msg;
	struct spi_transfer wake_xfer = {
		.len = 2,
		.speed_hz = 1000000,
		.bits_per_word = 8,
		.word_delay = { .value = 5, .unit = SPI_DELAY_UNIT_USECS },
	};
	u8 wake_tx[2] = { 0x19, 0xc1 }, wake_rx[2] = { 0 };
	struct spi_transfer hbpp_xfer = {
		.tx_buf = dummy_frame,
		.len = sizeof(dummy_frame),
		.speed_hz = 1000000,
		.bits_per_word = 8,
		.word_delay = { .value = 5, .unit = SPI_DELAY_UNIT_USECS },
	};
	struct spi_message hbpp_msg, long_msg, ack_msg;
	struct spi_transfer long_xfer = {
		.len = sizeof(fw_packet),
		.speed_hz = 1000000,
		.bits_per_word = 8,
		.word_delay = { .value = 5, .unit = SPI_DELAY_UNIT_USECS },
	};
	struct spi_transfer ack_xfer = {
		.len = 2,
		.speed_hz = 1000000,
		.bits_per_word = 8,
		.word_delay = { .value = 5, .unit = SPI_DELAY_UNIT_USECS },
	};
	u8 ack_tx[2] = { 0x1a, 0xa1 }, ack_rx[2] = { 0 };
 struct spi_transfer exec_xfer = {
  .len=12,.speed_hz=1000000,.bits_per_word=8,
  .word_delay={.value=5,.unit=SPI_DELAY_UNIT_USECS},
 };
 struct spi_message exec_msg;
 /* Z2 loader execute at exact J72 personality address0x10003400.
  * private matching method0xfffffff007e9a4cc; checksum sum bytes2..9.
  */
 u8 exec_tx[12]={0x1d,0x53,0x34,0,0x10,0,0,1,0,0,0,0x45};
 u8 exec_rx[12]={0};
 unsigned int poll_i,attn_edges=0;
 u32 attn_before,attn_prev,attn_now;

	u64 started_ns;
	u8 rx[sizeof(dummy_frame)] = { 0 };
	u8 hbpp_rx[sizeof(dummy_frame)] = { 0 };
	u16 word0, word1;
	bool reset_released = false;
	struct regmap *map;
	struct resource *res;
	void __iomem *pmgr, *gpio, *pwm;
	unsigned int reg84, reg85, reg398;
	u32 state, spi2_state, reset, before10, before14, before20, cs_saved, cs_mux;
	int ret, ldo_off, ana_off;
	bool rails_touched = false, cs_touched = false, sync_touched = false;
	u32 sync_saved = 0, sync_mux;

	if (!of_machine_is_compatible("apple,j72") || attempted)
		return -ENODEV;
	attempted = true;
	expected = of_find_node_by_path(PWM_PATH);
	domain = of_parse_phandle(pdev->dev.of_node, "power-domains", 0);
	expected_domain = of_find_node_by_path(DOMAIN_PATH);
	ret = expected && pdev->dev.of_node == expected && domain &&
	      domain == expected_domain ? 0 : -ENODEV;
	of_node_put(expected);
	of_node_put(domain);
	of_node_put(expected_domain);
	if (ret)
		return ret;
	res = platform_get_resource(pdev, IORESOURCE_MEM, 0);
	if (!res || res->start != PWM_PHYS || resource_size(res) != 0x1000)
		return -ENODEV;

	pmgr = ioremap(PMGR_PHYS, 0x1000);
	gpio = ioremap(GPIO_PHYS, 0x1000);
	if (!pmgr || !gpio) {
		ret = -ENOMEM;
		goto out_maps;
	}
	state = readl(pmgr + PWM0_PSTATE);
	spi2_state = readl(pmgr + SPI2_PSTATE);
	reset = readl(gpio + GPIO15_OFFSET);
	pr_info("j72_touch_session: PMGR_PWM0_ON=%08x PMGR_SPI2_IDLE=%08x GPIO15=%08x GPIO79=%08x\n",
		state, spi2_state, reset, readl(gpio + GPIO79_OFFSET));
	/* Linux has already attached and powered the PMGR domain for probe. */
	if (((state >> 4) & 0xf) != 0xf || ((spi2_state >> 4) & 0xf) != 0 ||
	    reset != 0x00072602 ||
	    readl(gpio + 119 * 4) != 0x00072602 ||
	    readl(gpio + GPIO79_OFFSET) != 0x00072203 ||
	    readl(gpio + 76 * 4) != 0x00072620 ||
	    readl(gpio + 77 * 4) != 0x00072620 ||
	    readl(gpio + 78 * 4) != 0x00072620) {
		ret = -EPERM;
		goto out_maps;
	}
	spi_node = of_find_node_by_path(SPI_PATH);
	if (!spi_node || !of_device_is_compatible(spi_node, "apple,s5l8960x-spi")) {
		of_node_put(spi_node);
		ret = -ENODEV;
		goto out_maps;
	}
	ctlr = of_find_spi_controller_by_node(spi_node);
	of_node_put(spi_node);
	if (!ctlr || ctlr->bus_num != 0 || ctlr->num_chipselect != 1) {
		ret = -ENODEV;
		goto out_maps;
	}
	pmic_node = of_find_node_by_path(PMIC_PATH);
	if (!pmic_node) {
		ret = -ENODEV;
		goto out_maps;
	}
	pmic_dev = bus_find_device(&i2c_bus_type, NULL, pmic_node, match_pmic);
	of_node_put(pmic_node);
	if (!pmic_dev) {
		ret = -ENODEV;
		goto out_maps;
	}
	map = dev_get_regmap(pmic_dev, NULL);
	ret = map ? regmap_read(map, 0x84, &reg84) : -ENODEV;
	if (!ret)
		ret = regmap_read(map, 0x85, &reg85);
	if (!ret)
		ret = regmap_read(map, 0x398, &reg398);
	if (ret) {
		pr_err("j72_touch_session: PMIC read failed: %d\n", ret);
		goto out_maps;
	}
	pr_info("j72_touch_session: PMIC84=%02x PMIC85=%02x PMIC398=%02x\n",
		reg84 & 0xff, reg85 & 0xff, reg398 & 0xff);
	if (reg84 != 0 || reg85 != 0x11 || reg398 != 0x14) {
		ret = -EPERM;
		goto out_maps;
	}
	pwm = devm_platform_ioremap_resource(pdev, 0);
	if (IS_ERR(pwm)) {
		ret = PTR_ERR(pwm);
		goto out_maps;
	}
	before10 = readl(pwm + 0x10);
	before14 = readl(pwm + 0x14);
	before20 = readl(pwm + 0x20);
	pr_info("j72_touch_session: BEFORE r10=%08x r14=%08x r20=%08x\n",
		before10, before14, before20);
	if (before10 || before14 || before20) {
		ret = -EBUSY;
		goto out_maps;
	}
	/* Program the previously tested PWM path before the rail sequence. */
	writel(COUNT_HALF, pwm + 0x10);
	writel(COUNT_HALF, pwm + 0x14);
	writel(0x4003, pwm + 0x20);
	state = readl(pwm + 0x20);
	pr_info("j72_touch_session: PWM_ACTIVE r10=%08x r14=%08x r20=%08x\n",
		readl(pwm + 0x10), readl(pwm + 0x14), state);
	if (state != 0x4001) {
		pr_err("j72_touch_session: unexpected PWM control readback; rails untouched\n");
		goto restore;
	}
	/* Exact J72 16H81 _setLDO, index13: after setting table mask04,
  * 8689ca4..8689cd8 ORs shared bits03 whenever any bits1c remain set.
  * Thus our guarded baseline84=00 must become07, not04. Restore all
  * owned bits07 to00; preserve voltage register398=14 throughout.
  * This is independent exact-J72 confirmation of the J86 reference.
  */
	rails_touched = true;
	ret = regmap_update_bits(map, 0x84, 0x07, 0x07);
	if (ret) {
		pr_err("j72_touch_session: analog enable failed: %d\n", ret);
		goto restore;
	}
	ret = regmap_update_bits(map, 0x85, 0x02, 0x02);
	if (ret) {
		pr_err("j72_touch_session: LDO enable failed: %d\n", ret);
		goto restore;
	}
	ret = regmap_read(map, 0x84, &reg84);
	if (!ret)
		ret = regmap_read(map, 0x85, &reg85);
	if (ret || reg84 != 7 || reg85 != 0x13) {
		pr_err("j72_touch_session: rail on readback failed: %d\n", ret);
		goto restore;
	}
	pr_info("j72_touch_session: RAILS_ON PMIC84=%02x PMIC85=%02x GPIO15=%08x\n",
		reg84 & 0xff, reg85 & 0xff, readl(gpio + GPIO15_OFFSET));
	spi_dev = spi_new_device(ctlr, &board);
	if (!spi_dev) {
		pr_err("j72_touch_session: SPI device registration failed\n");
		goto restore;
	}
	/* Tested separately: add bounded software setup and hold times. */
	spi_dev->cs_setup.value = AUDIT_CS_US;
	spi_dev->cs_setup.unit = SPI_DELAY_UNIT_USECS;
	spi_dev->cs_hold.value = AUDIT_CS_US;
	spi_dev->cs_hold.unit = SPI_DELAY_UNIT_USECS;
	ret = spi_setup(spi_dev);
	if (ret) goto restore;
	pr_info("j72_touch_session: CS_TIMING setup_us=%u hold_us=%u\n", AUDIT_CS_US, AUDIT_CS_US);
	/* J72 ADT routes SPI2 CS0 through GPIO79; boot left it GPIO-high.
	 * Save first so every subsequent failure restores the original pad.
	 */
	cs_saved = readl(gpio + GPIO79_OFFSET);
	if (cs_saved != 0x00072203)
		goto restore;
	cs_mux = (cs_saved & ~0x260U) | 0x220U;
	cs_touched = true;
	writel(cs_mux, gpio + GPIO79_OFFSET);
	state = readl(gpio + GPIO79_OFFSET);
	pr_info("j72_touch_session: CS_MUX saved=%08x requested=%08x readback=%08x\n",
		cs_saved, cs_mux, state);
	if ((state & 0x260U) != 0x220U)
		goto restore;
	/* J72's matching AppleMultitouchN1SPI contains a pre-reset
	 * REQ_WAKEUP helper. Wait for the clock, send it once, and keep
	 * the helper's 10 ms settle before the reset-held dummy.
	 */
	msleep(15);
	spi_message_init(&wake_msg);
	wake_xfer.tx_buf = wake_tx;
	wake_xfer.rx_buf = wake_rx;
	spi_message_add_tail(&wake_xfer, &wake_msg);
	ret = spi_sync(spi_dev, &wake_msg);
	pr_info("j72_touch_session: N1_WAKE ret=%d actual=%u rx=%02x%02x GPIO15=%08x\n",
		ret, wake_msg.actual_length, wake_rx[0], wake_rx[1],
		readl(gpio + GPIO15_OFFSET));
	if (ret || wake_msg.actual_length != sizeof(wake_tx) ||
	    readl(gpio + GPIO15_OFFSET) != reset ||
	    (readl(gpio + GPIO79_OFFSET) & 0x260U) != 0x220U)
		goto restore;
	/* Matching J72 N1 startup enables display_sync after wake and before
	 * reset. GPIO function argument3 selects descriptor mode2; the exact
	 * provider maps this to mask0x260/value0x220. Save and restore the pad.
	 */
	sync_saved = readl(gpio + 119 * 4);
	if (sync_saved != 0x00072602) goto restore;
	if (AUDIT_SYNC) {
	sync_mux = (sync_saved & ~0x260U) | 0x220U;
	sync_touched = true;
	writel(sync_mux, gpio + 119 * 4);
	state = readl(gpio + 119 * 4);
	pr_info("j72_touch_session: DISPLAY_SYNC saved=%08x requested=%08x readback=%08x\n",
		sync_saved, sync_mux, state);
	if ((state & ~1U) != (sync_mux & ~1U)) goto restore;
	}
	msleep(10);
	spi_message_init(&msg);
	xfer.rx_buf = rx;
	spi_message_add_tail(&xfer, &msg);
	ret = spi_sync(spi_dev, &msg);
	pr_info("j72_touch_session: SPI_DUMMY ret=%d actual=%u rx0=%02x%02x%02x%02x GPIO15=%08x\n",
		ret, msg.actual_length, rx[0], rx[1], rx[2], rx[3],
		readl(gpio + GPIO15_OFFSET));
	if (ret || msg.actual_length != sizeof(dummy_frame) ||
	    readl(gpio + GPIO15_OFFSET) != reset ||
	    (readl(gpio + GPIO79_OFFSET) & 0x260U) != 0x220U)
		goto restore;
	/* J72's OIPG reset descriptor is active-low: electrical high releases.
	 * The matching N1 startup waits 15 ms here and 15 ms before HBPP.
	 */
	writel(reset | 1, gpio + GPIO15_OFFSET);
	reset_released = true;
	pr_info("j72_touch_session: RESET_RELEASED=%08x ATTN_BEFORE=%08x\n",
		readl(gpio + GPIO15_OFFSET), readl(gpio + GPIO111_OFFSET));
	if (readl(gpio + GPIO15_OFFSET) != (reset | 1))
		goto restore;
	msleep(30);
	spi_message_init(&hbpp_msg);
	hbpp_xfer.rx_buf = hbpp_rx;
	spi_message_add_tail(&hbpp_xfer, &hbpp_msg);
	ret = spi_sync(spi_dev, &hbpp_msg);
	word0 = ((u16)hbpp_rx[0] << 8) | hbpp_rx[1];
	word1 = ((u16)hbpp_rx[2] << 8) | hbpp_rx[3];
	pr_info("j72_touch_session: HBPP ret=%d actual=%u words=%04x,%04x accepted=%d ATTN_AFTER=%08x\n",
		ret, hbpp_msg.actual_length, word0, word1,
		!ret && hbpp_msg.actual_length == sizeof(dummy_frame) &&
		hbpp_word_ok(word0) && hbpp_word_ok(word1) &&
		(word0 == 0x4879 || word1 == 0x4879),
		readl(gpio + GPIO111_OFFSET));
	if (ret || hbpp_msg.actual_length != sizeof(dummy_frame) ||
	    !hbpp_word_ok(word0) || !hbpp_word_ok(word1) ||
	    (word0 != 0x4879 && word1 != 0x4879))
		goto restore;
	if (!upload_calibration(spi_dev)) goto restore;
	/* Keep one continuous CS interval while exercising the RX FIFO
	 * service path. Execute and read-only info follow only validated replies.
	 */
	msleep(15);
	spi_message_init(&long_msg);
	long_xfer.tx_buf = fw_packet;
	long_xfer.rx_buf = fw_rx;
	spi_message_add_tail(&long_xfer, &long_msg);
	started_ns = ktime_get_ns();
	ret = spi_sync(spi_dev, &long_msg);
	pr_info("j72_touch_session: FW_TX ret=%d actual=%u elapsed_us=%llu\n",
		ret, long_msg.actual_length,
		(unsigned long long)div_u64(ktime_get_ns() - started_ns, 1000));
	if (ret || long_msg.actual_length != sizeof(fw_packet))
		goto restore;
	spi_message_init(&ack_msg);
	ack_xfer.tx_buf = ack_tx;
	ack_xfer.rx_buf = ack_rx;
	spi_message_add_tail(&ack_xfer, &ack_msg);
	ret = spi_sync(spi_dev, &ack_msg);
	pr_info("j72_touch_session: FW_ACK ret=%d actual=%u word=%04x accepted=%d\n",
		ret, ack_msg.actual_length, ((u16)ack_rx[0] << 8) | ack_rx[1],
		!ret && ack_msg.actual_length == 2 && ack_rx[0] == 0x4b && ack_rx[1] == 0xc1);

 if(ret || ack_msg.actual_length!=2 || ack_rx[0]!=0x4b || ack_rx[1]!=0xc1) goto restore;
 if (!read_chip_version(spi_dev)) goto restore;
 if (!perform_calibration(spi_dev)) goto restore;
 attn_before=readl(gpio+GPIO111_OFFSET);attn_prev=attn_before;
 spi_message_init(&exec_msg);exec_xfer.tx_buf=exec_tx;exec_xfer.rx_buf=exec_rx;
 spi_message_add_tail(&exec_xfer,&exec_msg);
 ret=spi_sync(spi_dev,&exec_msg);
 pr_info("j72_touch_session: EXECUTE ret=%d actual=%u reply=%*phN attn_before=%x\n",
 ret,exec_msg.actual_length,12,exec_rx,attn_before);
 if(!ret && exec_msg.actual_length==12) {
  for(poll_i=0;poll_i<100;poll_i++) {
   usleep_range(1000,1500);attn_now=readl(gpio+GPIO111_OFFSET);
   if((attn_now^attn_prev)&1) attn_edges++;
   attn_prev=attn_now;
  }
  pr_info("j72_touch_session: EXEC_ATTN_AFTER=%x edges=%u\n",attn_prev,attn_edges);
 }

 /* Exact J72 N1 device-info method: two identical 16-byte E2 frames,
  * checksum=sum(first14) in little-endian bytes14/15. No register write.
  * Source reference: private matching iOS function 0xfffffff007e9708c.
  */
 if (ret || exec_msg.actual_length != 12)
  goto restore;
 if (!read_device_info(spi_dev)) goto restore;
 query_report_metadata(spi_dev,gpio);

restore:
	if (reset_released) {
		writel(reset, gpio + GPIO15_OFFSET);
		pr_info("j72_touch_session: RESET_REASSERTED=%08x\n",
			readl(gpio + GPIO15_OFFSET));
		if (readl(gpio + GPIO15_OFFSET) != reset)
			cleanup_failed=true;
		if (readl(gpio + GPIO15_OFFSET) != reset)
			pr_err("j72_touch_session: reset restore not confirmed; reboot immediately\n");
	}
	if (sync_touched) {
		writel(sync_saved, gpio + 119 * 4);
		state = readl(gpio + 119 * 4);
		pr_info("j72_touch_session: DISPLAY_SYNC_RESTORED=%08x\n", state);
		if ((state & ~1U) != (sync_saved & ~1U))
			cleanup_failed=true;
		if ((state & ~1U) != (sync_saved & ~1U))
			pr_err("j72_touch_session: sync restore not confirmed; reboot immediately\n");
	}
	if (spi_dev)
		spi_unregister_device(spi_dev);
	if (cs_touched) {
		writel(cs_saved, gpio + GPIO79_OFFSET);
		state = readl(gpio + GPIO79_OFFSET);
		pr_info("j72_touch_session: CS_RESTORED=%08x\n", state);
		if (state != cs_saved)
			cleanup_failed=true;
		if (state != cs_saved)
			pr_err("j72_touch_session: CS restore not confirmed; reboot immediately\n");
	}
	/* iOS powers down LDO before analog; clear both even after a partial error. */
	if (rails_touched) {
		ldo_off = regmap_update_bits(map, 0x85, 0x02, 0);
		ana_off = regmap_update_bits(map, 0x84, 0x07, 0);
		ret = regmap_read(map, 0x84, &reg84);
		if (!ret)
			ret = regmap_read(map, 0x85, &reg85);
		if (!ret) ret = regmap_read(map, 0x398, &reg398);
		if (!ret && reg398 != 0x14) ret = -EIO;
		pr_info("j72_touch_session: RAILS_OFF PMIC84=%02x PMIC85=%02x err=%d,%d,%d\n",
			reg84 & 0xff, reg85 & 0xff, ldo_off, ana_off, ret);
		if (ldo_off || ana_off || ret || reg84 != 0 || reg85 != 0x11)
			cleanup_failed=true;
		if (ldo_off || ana_off || ret || reg84 != 0 || reg85 != 0x11)
			pr_err("j72_touch_session: rail restore not confirmed; reboot immediately\n");
	}
	/* Disable first, then restore the exact saved idle words. */
	writel(before20, pwm + 0x20);
	readl(pwm + 0x20);
	writel(before10, pwm + 0x10);
	writel(before14, pwm + 0x14);
	pr_info("j72_touch_session: RESTORED r10=%08x r14=%08x r20=%08x\n",
		readl(pwm + 0x10), readl(pwm + 0x14), readl(pwm + 0x20));
	if (readl(pwm+0x10)!=before10 || readl(pwm+0x14)!=before14 || readl(pwm+0x20)!=before20)cleanup_failed=true;
	/* Probe failure detaches and powers off the platform domain. */
	after_pmgr = pmgr;
	schedule_delayed_work(&after_work, msecs_to_jiffies(500));
	iounmap(gpio);
	put_device(pmic_dev);
	put_device(&ctlr->dev);
	return -ENODEV;
out_maps:
	if (ctlr)
		put_device(&ctlr->dev);
	if (pmic_dev)
		put_device(pmic_dev);
	if (gpio)
		iounmap(gpio);
	if (pmgr)
		iounmap(pmgr);
	pr_err("j72_touch_session: guard failed: %d; no PWM write\n", ret);
	return ret;
}

static const struct of_device_id pulse_match[] = {
	{ .compatible = "apple,j72-pwm0-observer" }, {}
};
static struct platform_driver pulse_driver = {
	.probe = pulse_probe,
	.driver = { .name = "j72-touch-session", .of_match_table = pulse_match },
};
static int session_worker(void *unused)
{
 while(!kthread_should_stop()){
  int ret;
  wait_event_interruptible(session_wait,kthread_should_stop() || READ_ONCE(enabled));
  if(kthread_should_stop())break;
  mutex_lock(&session_control);
  if(!enabled){mutex_unlock(&session_control);continue;}
  WRITE_ONCE(session_state,1);session_number++;
  WRITE_ONCE(stop_requested,false);
  mutex_unlock(&session_control);
  memset(&audit,0,sizeof(audit));audit.magic=0x4a373253;audit.version=3;
  attempted=false;cleanup_failed=false;cleanup_ok=false;
  input_frames=input_bad_layouts=input_timeouts=input_recoveries=0;
  layout_anomalies=wire_anomalies=0;
  /* Registration probes synchronously in THIS worker. The diagnostic probe
   * deliberately detaches after full restoration; init never blocks on it.
   */
  ret=platform_driver_register(&pulse_driver);
  if(!ret)platform_driver_unregister(&pulse_driver);
  msleep(550); /* Let the detached genpd finish its existing off transition. */
  flush_delayed_work(&after_work);
  mutex_lock(&session_control);
  WRITE_ONCE(enabled,false);
  if(ret || !attempted || audit.done!=1 || !audit.info_end || cleanup_failed){
   WRITE_ONCE(session_state,4);
   pr_err("j72_touch_session: SESSION_FAILED number=%u done=%u cleanup_failed=%u\n",session_number,audit.done,cleanup_failed);
  }else{
   cleanup_ok=true;
   WRITE_ONCE(session_state,0);
   pr_info("j72_touch_session: SESSION_IDLE number=%u health=1 cleanup=1\n",session_number);
  }
  mutex_unlock(&session_control);
 }
 return 0;
}
static int __init pulse_init(void)
{
 int ret;
 ret=sysfs_create_bin_file(kernel_kobj,&fw_rx_attr);if(ret)return ret;
 ret=sysfs_create_bin_file(kernel_kobj,&audit_attr);
 if(ret){sysfs_remove_bin_file(kernel_kobj,&fw_rx_attr);return ret;}
 session_thread=kthread_run(session_worker,NULL,"j72-touch");
 if(IS_ERR(session_thread)){
  ret=PTR_ERR(session_thread);sysfs_remove_bin_file(kernel_kobj,&fw_rx_attr);
  sysfs_remove_bin_file(kernel_kobj,&audit_attr);return ret;
 }
 return 0;
}
static void __exit pulse_exit(void)
{
 WRITE_ONCE(enabled,false);WRITE_ONCE(stop_requested,true);
 wake_up_interruptible(&session_wait);kthread_stop(session_thread);
 cancel_delayed_work_sync(&after_work);
 if(after_pmgr)iounmap(after_pmgr);
 sysfs_remove_bin_file(kernel_kobj,&fw_rx_attr);sysfs_remove_bin_file(kernel_kobj,&audit_attr);
}
module_init(pulse_init);
module_exit(pulse_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("One-shot J72 N1 wake and touch firmware ACK diagnostic in RAM");

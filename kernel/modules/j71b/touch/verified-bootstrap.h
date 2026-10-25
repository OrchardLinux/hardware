/* SPDX-License-Identifier: GPL-2.0-only
 * J71b firmware upload and DeviceInfo handshake.
 * Resource acquisition is handled by the calling touchscreen driver.
 */
struct j71b_readiness {
	struct spi_device *spi;
	struct gpio_desc *reset;
	struct gpio_desc *ana;
	struct gpio_desc *ldo;
	struct clk *clk;
	struct completion irq;
	bool clock_on;
	bool irq_on;
	atomic_t irq_count;
	struct j71b_fw_plan plan;
	u8 ack_reply[18];
	unsigned int ack_count;
};
static irqreturn_t j71b_readiness_irq(int irq, void *data)
{
	struct j71b_readiness *test = data;

	atomic_inc(&test->irq_count);
	complete(&test->irq);
	return IRQ_HANDLED;
}

static void j71b_readiness_stop(struct j71b_readiness *test)
{
	if (test->irq_on) {
		disable_irq(test->spi->irq);
		test->irq_on = false;
	}
	gpiod_set_value_cansleep(test->reset, 1);
	if (test->clock_on) {
		clk_disable_unprepare(test->clk);
		test->clock_on = false;
	}
	gpiod_set_value_cansleep(test->ldo, 0);
	gpiod_set_value_cansleep(test->ana, 0);
	dev_info(&test->spi->dev,
		 "J71B_START_STOP reset=asserted touch-power=off\n");
}

/* Same blob word selection, ACK and delays as J172. No init ACK. */
static int upload_packets(struct j71b_readiness *test,u8 *ack)
{
 unsigned int i,before,after;
 int ret;
 ack[0]=0x1a;ack[1]=0xa1;
 for(i=2;i<=10;i++){
  const struct j71b_fw_record *r=&test->plan.records[i];
  struct spi_transfer blob={.tx_buf=r->data,.len=r->size,
    .bits_per_word=r->size>=17?16:8,.speed_hz=4000000};
  struct spi_transfer response={.tx_buf=ack,.rx_buf=test->ack_reply+2*(i-2),
    .len=2,.bits_per_word=8,.speed_hz=4000000};
  reinit_completion(&test->irq);
  before=apple_sio_payload_submits();
  dev_info(&test->spi->dev,"J71B_START_PACKET index=%u bytes=%u bpw=%u\n",i,r->size,blob.bits_per_word);
  ret=spi_sync_transfer(test->spi,&blob,1);
  if(ret)return ret;
  after=apple_sio_payload_submits();
  if(after-before!=(r->size>=17?1:0))return -EPROTO;
  ret=spi_sync_transfer(test->spi,&response,1);
  if(ret)return ret;
  test->ack_count++;
  usleep_range(10,110);
  if(i!=10)msleep(20); /* Reference's masked-IRQ bounded wait, not a retry. */
 }
 enable_irq(test->spi->irq);test->irq_on=true;
 for(i=0;i<1;i++){
  if(!wait_for_completion_timeout(&test->irq,msecs_to_jiffies(5000))){
   dev_err(&test->spi->dev,"J71B_START_READY_TIMEOUT expected-edge=%u observed-irqs=%d\n",i+1,atomic_read(&test->irq_count));
   return -ETIMEDOUT;
  }
  dev_info(&test->spi->dev,"J71B_START_READY edge=%u\n",i+1);
 }
 disable_irq(test->spi->irq);test->irq_on=false;
 return 0;
}

static int device_info(struct j71b_readiness *test,u8 *tx,u8 *rx)
{
 static const u8 commands[]={0xee,0xe2,0xe1};
 unsigned int i,j;
 u16 sum;
 int ret;
 struct spi_transfer xfer={.tx_buf=tx,.rx_buf=rx,.len=16,.bits_per_word=8,.speed_hz=4000000};
 for(i=0;i<3;i++){
  memset(tx,0,16);memset(rx,0,16);tx[0]=commands[i];
  put_unaligned_le16(commands[i],tx+14);
  ret=spi_sync_transfer(test->spi,&xfer,1);if(ret)return ret;
  if(i==0)usleep_range(2000,2100);
  if(i==1)usleep_range(125,225);
 }
 sum=0;for(j=0;j<14;j++)sum+=rx[j];
 if(rx[0]!=0xe2 || get_unaligned_le16(rx+14)!=sum){
  dev_err(&test->spi->dev,"J71B_START_DEVICE_INFO_INVALID reply=%*ph\n",16,rx);
  return -EPROTO;
 }
 dev_info(&test->spi->dev,"J71B_START_DEVICE_INFO platform=%u interface=%u max-packet=%u\n",rx[1],rx[2],get_unaligned_le16(rx+3));
 return 0;
}

static int j71b_verified_transport(struct j71b_readiness *test,u8 *tx,u8 *rx)
{
 struct spi_device *spi=test->spi;
 struct device *dev=&spi->dev;
 struct spi_transfer xfer={.len=4,.bits_per_word=8,.speed_hz=500000};
 int ret;
 memset(tx,0,16);memset(rx,0,16);
 init_completion(&test->irq);atomic_set(&test->irq_count,0);
	dev_info(dev, "J71B_START_BEGIN single-attempt max-speed=%u readiness-speed=500000 sync-pins=untouched\n",
		 spi->max_speed_hz);
	ret = gpiod_direction_output(test->reset, 1);
	if (ret)
		return dev_err_probe(dev, ret, "cannot assert reset\n");
	ret = gpiod_direction_output(test->ana, 0);
	if (ret)
		return dev_err_probe(dev, ret, "cannot set analog power off\n");
	ret = gpiod_direction_output(test->ldo, 0);
	if (ret)
		return dev_err_probe(dev, ret, "cannot set LDO power off\n");
	msleep(50);
	gpiod_set_value_cansleep(test->ana, 1);
	usleep_range(10000, 11000);
	gpiod_set_value_cansleep(test->ldo, 1);
	usleep_range(1000, 2000);
	ret = clk_prepare_enable(test->clk);
	if (ret)
		goto stop;
	test->clock_on = true;
	usleep_range(15000, 16000);
	dev_info(dev, "J71B_GATE_POWERED touch-clock=%lu\n", clk_get_rate(test->clk));

	/* Reference zero preamble under reset. This short exchange is PIO; only
	 * the subsequent long16-bit payloads may use the reviewed DMA provider.
	 */
	xfer.tx_buf = tx;
	xfer.rx_buf = NULL;
	ret = spi_sync_transfer(spi, &xfer, 1);
	if (ret)
		goto stop;
	dev_info(dev, "J71B_GATE_PREAMBLE_OK\n");
	reinit_completion(&test->irq);
	enable_irq(spi->irq);
	test->irq_on = true;
	gpiod_set_value_cansleep(test->reset, 0);
	if (!wait_for_completion_timeout(&test->irq, msecs_to_jiffies(5000))) {
		ret = -ETIMEDOUT;
		dev_err(dev, "J71B_GATE_FAIL stage=boot-irq timeout-ms=5000\n");
		goto stop;
	}
	dev_info(dev, "J71B_GATE_BOOT_IRQ_OK\n");
	/* Reference masks touch IRQ during upload. No ACK follows init; ACKs
	 * follow each later data packet. No alternate encoding or retry.
	 */
	disable_irq(spi->irq);
	test->irq_on = false;
	tx[0] = 0x1a; tx[1] = 0xa1; tx[2] = 0x18; tx[3] = 0xe1;
	xfer.rx_buf = rx;
	ret = spi_sync_transfer(spi, &xfer, 1);
	if (ret)
		goto stop;
	dev_info(dev, "J71B_GATE_REPLY %*ph\n", 4, rx);
	if (rx[0] != 0x1f || rx[1] != 0x01) {
		ret = -EPROTO;
		dev_err(dev, "J71B_GATE_FAIL stage=readiness reply-unexpected\n");
		goto stop;
	}
	dev_info(dev,"J71B_START_HBPP_READY no-init-ack sync-pins=untouched\n");
	dev_info(dev,"J71B_START_IRQ_BASELINE count=%d\n",atomic_read(&test->irq_count));
	ret=upload_packets(test,tx);
	if(ret)goto stop;
	ret=device_info(test,tx,rx);
	if(!ret)dev_info(dev,"J71B_START_PASS firmware-and-calibration-ready DeviceInfo-valid input=disabled\n");
	if (!ret) return 0; /* Caller now owns powered resources, IRQ disabled. */
stop:
	if (ret)
		dev_err(dev, "J71B_GATE_END error=%d; no retry\n", ret);
	j71b_readiness_stop(test);
	dev_info(dev,"J71B_START_ACK_REPLIES count=%u bytes=%*ph\n",
		 test->ack_count,2*test->ack_count,test->ack_reply);
	return ret;
}

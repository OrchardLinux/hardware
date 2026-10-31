#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>
typedef uint8_t u8;
#define HDQ_UART_BYTE_LEN 8
#define HDQ_UART_ONE 0xfe
#define HDQ_UART_ZERO 0xc0
#define HDQ_UART_ONE_MIN 0xf0
#define HDQ_CMD_WRITE 0x80
#define HDQ_CMD_REG_MASK 0x7f
struct hdq_uart { int rx_lock,rx_done,rx_want,rx_count,xfer_lock;u8 rx_start[8];u8 *rx_buf; };
struct serdev_device { struct hdq_uart *data; };
static struct hdq_uart *serdev_device_get_drvdata(struct serdev_device *s){return s->data;}
#define spin_lock_irqsave(lock,flags) do{(void)(lock);(flags)=0;}while(0)
#define spin_unlock_irqrestore(lock,flags) do{(void)(lock);(void)(flags);}while(0)
static void complete(int *done){(*done)++;}
static void mutex_lock(int *lock){(void)lock;}
static void mutex_unlock(int *lock){(void)lock;}
static unsigned tx_calls;
static u8 last_tx[16];
static int hdq_transact(struct hdq_uart *h,const u8 *tx,int n,u8 *resp){
 (void)h;if(n!=16||resp)abort();tx_calls++;memcpy(last_tx,tx,16);return 0;
}
static void hdq_encode_byte(u8 byte, u8 *out)
{
	int i;

	for (i = 0; i < HDQ_UART_BYTE_LEN; i++)
		out[i] = (byte & (1 << i)) ? HDQ_UART_ONE : HDQ_UART_ZERO;
}

static int hdq_decode_byte(const u8 *in, u8 *byte)
{
	u8 val = 0;
	int i;

	/* Preserve the working reference's threshold decoder. UART sampling
	 * can yield non-canonical low symbols (for example0xc3); do not
	 * invent a stricter pulse-shape requirement for the kernel frontend.
	 */
	for (i = 0; i < HDQ_UART_BYTE_LEN; i++)
		if (in[i] >= HDQ_UART_ONE_MIN)
			val |= (1 << i);
	*byte = val;
	return 0;
}

static size_t hdq_uart_receive_buf(struct serdev_device *serdev,
				   const u8 *data, size_t count)
{
	struct hdq_uart *hdq = serdev_device_get_drvdata(serdev);
	unsigned long flags;
	size_t consumed = 0;

	spin_lock_irqsave(&hdq->rx_lock, flags);
	while (consumed < count) {
		if (!hdq->rx_want) {
			consumed = count;
			break;
		}

		if (hdq->rx_count < hdq->rx_want)
			hdq->rx_buf[hdq->rx_count++] = data[consumed];
		consumed++;

		/* Require a contiguous command echo, across callback boundaries.
		 * A mismatching byte must not leave an old partial prefix valid.
		 * Retain a seven-byte sliding window until the real echo arrives.
		 */
		if (hdq->rx_count == HDQ_UART_BYTE_LEN &&
		    memcmp(hdq->rx_buf, hdq->rx_start, HDQ_UART_BYTE_LEN)) {
			memmove(hdq->rx_buf, hdq->rx_buf + 1, HDQ_UART_BYTE_LEN - 1);
			hdq->rx_count--;
		}

		if (hdq->rx_count >= hdq->rx_want)
			complete(&hdq->rx_done);
	}
	spin_unlock_irqrestore(&hdq->rx_lock, flags);

	return consumed;
}

static int hdq_write_byte(struct hdq_uart *hdq, u8 reg, u8 val)
{
	u8 cmd = HDQ_CMD_WRITE | (reg & HDQ_CMD_REG_MASK);
	u8 tx[2 * HDQ_UART_BYTE_LEN];
	int ret;

	/* Sealed-safe volatile DEVICE_TYPE0001 only: no arbitrary writes. */
	if (!((reg == 0x00 && val == 0x01) ||
	      (reg == 0x01 && val == 0x00)))
		return -EACCES;
	hdq_encode_byte(cmd, tx);
	hdq_encode_byte(val, tx + HDQ_UART_BYTE_LEN);

	mutex_lock(&hdq->xfer_lock);
	ret = hdq_transact(hdq, tx, sizeof(tx), NULL);
	mutex_unlock(&hdq->xfer_lock);

	return ret;
}


static void rx_test(const u8 *stream,size_t size,size_t split,const u8 *command,const u8 *expected,int should_complete){
 u8 buffer[24]={0};struct hdq_uart h={.rx_want=16,.rx_buf=buffer};struct serdev_device dev={.data=&h};memcpy(h.rx_start,command,8);
 if(hdq_uart_receive_buf(&dev,stream,split)!=split)abort();
 if(hdq_uart_receive_buf(&dev,stream+split,size-split)!=size-split)abort();
 if((h.rx_done!=0)!=should_complete)abort();
 if(should_complete&&(h.rx_count!=16||memcmp(buffer,expected,16)))abort();
 h.rx_want=0;h.rx_buf=NULL;if(hdq_uart_receive_buf(&dev,stream,size)!=size)abort();
}
int main(void){
 for(unsigned v=0;v<256;v++){u8 symbols[8],out;hdq_encode_byte(v,symbols);if(hdq_decode_byte(symbols,&out)||out!=v)abort();
  for(int i=0;i<8;i++)symbols[i]=(v&(1u<<i))?0xf8:0xc3;
  if(hdq_decode_byte(symbols,&out)||out!=v)abort();
 }
 u8 command[8],response[8],expected[16],noise[18]={0,0xff},broken[17],recovered[33];hdq_encode_byte(0x2c,command);hdq_encode_byte(0xa5,response);
 memcpy(expected,command,8);memcpy(expected+8,response,8);memcpy(noise+2,expected,16);
 memcpy(broken,command,3);broken[3]=0x55;memcpy(broken+4,command+3,5);memcpy(broken+9,response,8);
 memcpy(recovered,broken,17);memcpy(recovered+17,expected,16);
 for(size_t i=0;i<=sizeof noise;i++)rx_test(noise,sizeof noise,i,command,expected,1);
 for(size_t i=0;i<=sizeof broken;i++)rx_test(broken,sizeof broken,i,command,expected,0);
 for(size_t i=0;i<=sizeof recovered;i++)rx_test(recovered,sizeof recovered,i,command,expected,1);
 struct hdq_uart h={0};unsigned allowed=0;
 for(unsigned reg=0;reg<256;reg++)for(unsigned value=0;value<256;value++){
  bool ok=(reg==0&&value==1)||(reg==1&&value==0);tx_calls=0;
  int ret=hdq_write_byte(&h,reg,value);if((ret==0)!=ok||tx_calls!=(ok?1u:0u))abort();
  if(ok){u8 cmd,data;if(hdq_decode_byte(last_tx,&cmd)||hdq_decode_byte(last_tx+8,&data)||cmd!=(reg|0x80)||data!=value)abort();allowed++;}
 }
 printf("PASS: 512 actual encode/decode cases; 71 callback chunk/noise/prefix cases; 65536 write pairs checked, exactly %u volatile ID pairs allowed; no hardware accessed\n",allowed);
}

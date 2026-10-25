// SPDX-License-Identifier: GPL-2.0-or-later
/* Expected local rejection + drain-before-error path. Refuses payload enable. */
#include <linux/atomic.h>
#include <linux/completion.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/scatterlist.h>
#include "quiesce-api.h"

static atomic_t attempted = ATOMIC_INIT(0);
static int result = -EINPROGRESS;
module_param(result, int, 0444);
MODULE_PARM_DESC(result, "0 only for expected denied submission and safe cleanup");
struct denied_completion {
	struct completion done;
	struct dmaengine_result result;
};
static void denied_complete(void *data, const struct dmaengine_result *status)
{
	struct denied_completion *ctx = data;
	ctx->result = status ? *status : (struct dmaengine_result){.result = DMA_TRANS_ABORTED};
	complete(&ctx->done);
}

static int denied_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct of_phandle_args spec;
	struct dma_chan *chan;
	struct dma_async_tx_descriptor *tx;
	struct denied_completion ctx;
	struct scatterlist sg;
	struct dma_slave_config config = {.direction = DMA_MEM_TO_DEV, .dst_addr_width = DMA_SLAVE_BUSWIDTH_1_BYTE};
	dma_addr_t address;
	dma_cookie_t cookie;
	void *buffer;
	bool target;
	int ret;

	if (!of_machine_is_compatible("apple,j71b") ||
	    !of_property_read_bool(dev->of_node, "apple,diagnostics-only") ||
	    apple_sio_payload_allowed())
		return -EPERM;
	ret = of_parse_phandle_with_args(dev->of_node, "dmas", "#dma-cells", 0, &spec);
	if (ret) return ret;
	target = of_device_is_compatible(spec.np, "apple,t8010-sio") &&
		 spec.args_count == 1 && spec.args[0] == 26;
	of_node_put(spec.np);
	if (!target) return -EINVAL;
	if (atomic_xchg(&attempted, 1)) return -EALREADY;
	chan = dma_request_chan(dev, "tx");
	if (IS_ERR(chan)) { result = PTR_ERR(chan); return -ENODEV; }
	ret = dmaengine_slave_config(chan, &config);
	if (ret) goto release;
	buffer = dma_alloc_coherent(chan->device->dev, 32, &address, GFP_KERNEL);
	if (!buffer) { ret = -ENOMEM; goto release; }
	memset(buffer, 0, 32);
	sg_init_table(&sg, 1);
	sg_dma_address(&sg) = address;
	sg_dma_len(&sg) = 32;
	init_completion(&ctx.done);
	ctx.result.result = DMA_TRANS_ABORTED;
	tx = dmaengine_prep_slave_sg(chan, &sg, 1, DMA_MEM_TO_DEV,
				    DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!tx) { ret = -ENOMEM; goto free; }
	tx->callback_result = denied_complete;
	tx->callback_param = &ctx;
	cookie = dmaengine_submit(tx);
	if (dma_submit_error(cookie)) {
		ret = cookie;
		dmaengine_desc_free(tx);
		goto free;
	}
	dev_info(dev, "SIO_DENIED_TEST_BEGIN local-bytes=32 wire-payload=disabled\n");
	dma_async_issue_pending(chan);
	ret = wait_for_completion_timeout(&ctx.done, msecs_to_jiffies(5000)) ? 0 : -ETIMEDOUT;
	if (ret) dev_err(dev, "SIO_DENIED_TEST_TIMEOUT synchronizing; do not free/unload unknown state\n");
	/* The revised provider may hold this indefinitely if no drain is known.
	 * Buffer, stack callback context and channel remain alive until it returns.
	 */
	dmaengine_terminate_sync(chan);
	if (!ret && (ctx.result.result != DMA_TRANS_WRITE_FAILED || ctx.result.residue != 32)) ret = -EPROTO;
 free:
	dma_free_coherent(chan->device->dev, 32, buffer, address);
 release:
	dma_release_channel(chan);
	result = ret;
	if (!ret) dev_info(dev, "SIO_DENIED_TEST_PASS expected-error-after-drain buffer-and-channel-released payload-bytes=0\n");
	else dev_err(dev, "SIO_DENIED_TEST_FAIL status=%d\n", ret);
	return ret;
}
static const struct of_device_id denied_match[] = {
	{ .compatible = "apple,j71b-sio-channel-check" }, {}
};
MODULE_DEVICE_TABLE(of, denied_match);
static struct platform_driver denied_driver = {
	.probe = denied_probe,
	.driver = {.name = "j71b-sio-denied-test", .of_match_table = denied_match},
};
module_platform_driver(denied_driver);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("J71b expected-denial DMA lifetime diagnostic; requires payload gate OFF");

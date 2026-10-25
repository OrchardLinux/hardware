// SPDX-License-Identifier: GPL-2.0-only OR MIT
/* Apple T8010 SmartIO DMA controller
 * Copyright (C) 2026 Paul Praschl
 *
 * Uses the J172 configuration/SG wire format. Synchronizes issue/finish lifetime
 * and retains active resources until DMA has drained.
 * Initial issue remains synchronous, preserving the reference SPI arm ordering.
 * An abort poisons this channel until module reload. No automatic retry.
 */
#ifdef SIO_DMA_MODEL
#include "dma-test-kernel.h"
#else
#include <linux/completion.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/err.h>
#include <linux/module.h>
#include <linux/of_dma.h>
#include <linux/platform_device.h>
#include <linux/scatterlist.h>
#include <linux/slab.h>
#include <linux/workqueue.h>
#include <linux/soc/apple/sio.h>
#include "virt-dma.h"
#include "quiesce-api.h"
#endif
#include "report-policy.h"

#define APPLE_SIO_DMA_TX_ENDPOINT 0x1a
#define APPLE_SIO_DMA_OP_CONFIGURE 0x05
#define APPLE_SIO_DMA_OP_SUBMIT 0x06
#define APPLE_SIO_DMA_OP_ACK 0x65
#define APPLE_SIO_DMA_OP_COMPLETE 0x68
#define APPLE_SIO_DMA_OP5_SLOTS 8
#define APPLE_SIO_DMA_SG_OVERHEAD_SLOTS 6
#define APPLE_SIO_DMA_CONFIG_TIMEOUT_MS 1000

struct apple_sio_dma;
struct apple_sio_dma_chan;
struct apple_sio_dma_desc {
 struct virt_dma_desc vd;
 struct apple_sio_dma_chan *chan;
 struct apple_sio_async *request;
 u32 first_slot;
 unsigned int slots;
 size_t length;
 int status;
 u64 response;
 bool reported;
 bool terminated;
};
struct apple_sio_dma_chan {
 struct virt_dma_chan vc;
 struct apple_sio_dma *host;
 struct apple_sio_dma_desc *active;
 struct work_struct issue_work;
 struct work_struct finish_work;
 struct completion submit_idle;
 bool submitting;
 bool finish_queued;
 u8 endpoint;
 bool configured;
 bool poisoned;
 bool finishing;
};
struct apple_sio_dma {
 struct device *dev;
 struct apple_sio *sio;
 struct dma_device dma;
 struct apple_sio_dma_chan tx;
};

static struct apple_sio_dma_chan *to_apple_sio_dma_chan(struct dma_chan *chan)
{ return container_of(chan, struct apple_sio_dma_chan, vc.chan); }
static struct apple_sio_dma_desc *to_apple_sio_dma_desc(struct virt_dma_desc *vd)
{ return container_of(vd, struct apple_sio_dma_desc, vd); }

static void apple_sio_dma_desc_free(struct virt_dma_desc *vd)
{
 struct apple_sio_dma_desc *desc = to_apple_sio_dma_desc(vd);
 WARN_ON(desc->request);
 apple_sio_desc0_free(desc->chan->host->sio, desc->first_slot, desc->slots);
 kfree(desc);
}

#ifndef SIO_DMA_MODEL
static int apple_sio_dma_configure_channel(struct apple_sio_dma_chan *chan)
{
 static const u32 config[] = {0, 0, 0x80, 0x40, 0x80, 0, 0, 0};
 struct apple_sio_dma *host = chan->host;
 u32 first_slot, *descriptor;
 int ret;
 if (chan->configured) return 0;
 descriptor = apple_sio_desc0_alloc(host->sio, APPLE_SIO_DMA_OP5_SLOTS, &first_slot);
 if (!descriptor) return -ENOMEM;
 memcpy(descriptor, config, sizeof(config));
 descriptor[14] = 0;
 dma_wmb();
 ret = apple_sio_request(host->sio, chan->endpoint, APPLE_SIO_DMA_OP_CONFIGURE,
                        0, first_slot, APPLE_SIO_DMA_OP_ACK,
                        msecs_to_jiffies(APPLE_SIO_DMA_CONFIG_TIMEOUT_MS), NULL);
 apple_sio_desc0_free(host->sio, first_slot, APPLE_SIO_DMA_OP5_SLOTS);
 if (!ret) chan->configured = true;
 return ret;
}
static int apple_sio_dma_alloc_chan_resources(struct dma_chan *dma_chan)
{
 struct apple_sio_dma_chan *chan = to_apple_sio_dma_chan(dma_chan);
 if (READ_ONCE(chan->poisoned)) return -ESHUTDOWN;
 return apple_sio_dma_configure_channel(chan);
}
#endif

/* IRQ callback records only. No object is freed and no client callback is
 * published here. Finish work waits for issue bookkeeping and request->done.
 */
/* Called with vc.lock held. No scheduling tail may outlive this active epoch. */
static void apple_sio_dma_queue_finish(struct apple_sio_dma_chan *chan)
{
 if (!chan->submitting && !chan->finish_queued && !chan->finishing) {
  chan->finish_queued = true;
  schedule_work(&chan->finish_work);
 }
}

static void apple_sio_dma_request_done(void *data, int status, u64 response)
{
 struct apple_sio_dma_desc *desc = data;
 struct apple_sio_dma_chan *chan = desc->chan;
 unsigned long flags;
 spin_lock_irqsave(&chan->vc.lock, flags);
 if (!desc->reported) {
  desc->reported = true;
  desc->status = status;
  desc->response = response;
  if (status || !j71b_dma_full_report(response, desc->length)) chan->poisoned = true;
  apple_sio_dma_queue_finish(chan);
 }
 spin_unlock_irqrestore(&chan->vc.lock, flags);
}

/* Preserve J172's synchronous initial mailbox issue. The SPI caller enables
 * controller DMA only AFTER device_issue_pending returns. Never defer that
 * initial command to an unbounded worker scheduling delay.
 */
static void apple_sio_dma_start_next(struct apple_sio_dma_chan *chan)
{
 struct apple_sio_dma_desc *desc;
 struct apple_sio_async *request;
 struct virt_dma_desc *vd;
 unsigned long flags;
 spin_lock_irqsave(&chan->vc.lock, flags);
 if (chan->active || chan->poisoned) goto unlock;
 vd = vchan_next_desc(&chan->vc);
 if (!vd) goto unlock;
 list_del_init(&vd->node);
 desc = to_apple_sio_dma_desc(vd);
 chan->active = desc;
 chan->submitting = true;
 reinit_completion(&chan->submit_idle);
 spin_unlock_irqrestore(&chan->vc.lock, flags);
 dma_wmb();
 request = apple_sio_request_async_atomic(chan->host->sio, chan->endpoint,
        APPLE_SIO_DMA_OP_SUBMIT, 0, desc->first_slot, APPLE_SIO_DMA_OP_COMPLETE,
        apple_sio_dma_request_done, desc);
 if (IS_ERR(request))
  apple_sio_dma_request_done(desc, PTR_ERR(request), 0);
 spin_lock_irqsave(&chan->vc.lock, flags);
 if (!IS_ERR(request)) desc->request = request;
 chan->submitting = false;
 /* Publish under lock: no newer epoch can reinitialize this completion until
  * this publisher is finished. No desc dereference after the lock is released.
  */
 complete_all(&chan->submit_idle);
 if (desc->reported || desc->terminated) apple_sio_dma_queue_finish(chan);
 spin_unlock_irqrestore(&chan->vc.lock, flags);
 return;
 unlock:
 spin_unlock_irqrestore(&chan->vc.lock, flags);
}

static void apple_sio_dma_issue_work(struct work_struct *work)
{
 struct apple_sio_dma_chan *chan = container_of(work, struct apple_sio_dma_chan, issue_work);
 apple_sio_dma_start_next(chan);
}

static void apple_sio_dma_finish_work(struct work_struct *work)
{
 struct apple_sio_dma_chan *chan = container_of(work, struct apple_sio_dma_chan, finish_work);
 struct apple_sio_dma_desc *desc;
 struct apple_sio_async *request;
 unsigned long flags;
 bool full, issue_next;

 /* A stale wake cannot permit observing a newer epoch's unassigned handle. */
 for (;;) {
  wait_for_completion(&chan->submit_idle);
  spin_lock_irqsave(&chan->vc.lock, flags);
  if (!chan->submitting) break;
  spin_unlock_irqrestore(&chan->vc.lock, flags);
 }
 desc = chan->active;
 if (!desc || (!desc->reported && !desc->terminated)) {
  chan->finish_queued = false;
  spin_unlock_irqrestore(&chan->vc.lock, flags);
  return;
 }
 chan->finishing = true;
 full = desc->reported && !desc->status && j71b_dma_full_report(desc->response, desc->length);
 if (!full) chan->poisoned = true;
 request = desc->request;
 spin_unlock_irqrestore(&chan->vc.lock, flags);

 if (!full) {
  dev_warn(chan->host->dev, "SIO_DMA_ABORT_WAIT retaining active descriptor until drain\n");
  apple_sio_quiesce26(chan->host->sio);
 }
 /* Cancel only AFTER real retirement/drain. Even if the callback already ran,
  * this waits for core request->done, so no provider callback tail survives
  * publication of a client completion or descriptor release.
  */
 if (request) {
  apple_sio_async_cancel(request);
  apple_sio_async_put(request);
 }
 spin_lock_irqsave(&chan->vc.lock, flags);
 desc->request = NULL;
 chan->active = NULL;
 chan->finishing = false;
 chan->finish_queued = false;
 issue_next = full && !chan->poisoned;
 if (desc->terminated) {
  vchan_terminate_vdesc(&desc->vd);
 } else {
  desc->vd.tx_result.result = full && !chan->poisoned ? DMA_TRANS_NOERROR : DMA_TRANS_WRITE_FAILED;
  desc->vd.tx_result.residue = desc->vd.tx_result.result == DMA_TRANS_NOERROR ? 0 : desc->length;
  vchan_cookie_complete(&desc->vd);
 }
 spin_unlock_irqrestore(&chan->vc.lock, flags);
 /* desc can be freed by the callback tasklet now. Never touch it again. */
 if (issue_next) schedule_work(&chan->issue_work);
}

static void apple_sio_dma_issue_pending(struct dma_chan *dma_chan)
{
 struct apple_sio_dma_chan *chan = to_apple_sio_dma_chan(dma_chan);
 unsigned long flags;
 bool issued;
 spin_lock_irqsave(&chan->vc.lock, flags);
 issued = !chan->poisoned && vchan_issue_pending(&chan->vc);
 spin_unlock_irqrestore(&chan->vc.lock, flags);
 if (issued) apple_sio_dma_start_next(chan);
}

#ifndef SIO_DMA_MODEL
/* Original J172/T8010 wire descriptor layout, unchanged. */
static struct dma_async_tx_descriptor *apple_sio_dma_prep_slave_sg(
 struct dma_chan *dma_chan, struct scatterlist *sgl, unsigned int sg_len,
 enum dma_transfer_direction direction, unsigned long flags, void *context)
{
 struct apple_sio_dma_chan *chan = to_apple_sio_dma_chan(dma_chan);
 struct apple_sio_dma_desc *desc;
 struct scatterlist *sg;
 size_t total_length = 0;
 unsigned int slots, i;
 u32 first_slot, *descriptor, *segments;
 if (direction != DMA_MEM_TO_DEV || !sg_len || READ_ONCE(chan->poisoned) ||
     sg_len > 0x0aaa - APPLE_SIO_DMA_SG_OVERHEAD_SLOTS || !chan->configured) return NULL;
 slots = APPLE_SIO_DMA_SG_OVERHEAD_SLOTS + sg_len;
 descriptor = apple_sio_desc0_alloc(chan->host->sio, slots, &first_slot);
 if (!descriptor) return NULL;
 desc = kzalloc_obj(*desc, GFP_NOWAIT);
 if (!desc) { apple_sio_desc0_free(chan->host->sio, first_slot, slots); return NULL; }
 descriptor[14] = 0;
 descriptor[15] = sg_len;
 segments = descriptor + APPLE_SIO_DMA_SG_OVERHEAD_SLOTS * 3;
 for_each_sg(sgl, sg, sg_len, i) {
  dma_addr_t addr = sg_dma_address(sg);
  u32 len = sg_dma_len(sg);
  if (!len || total_length > SIZE_MAX - len) goto out_free;
  segments[3*i] = lower_32_bits(addr);
  segments[3*i+1] = upper_32_bits(addr);
  segments[3*i+2] = len;
  total_length += len;
 }
 desc->length = total_length;
 desc->chan = chan;
 desc->first_slot = first_slot;
 desc->slots = slots;
 return vchan_tx_prep(&chan->vc, &desc->vd, flags);
 out_free:
 apple_sio_desc0_free(chan->host->sio, first_slot, slots);
 kfree(desc);
 return NULL;
}

static enum dma_status apple_sio_dma_tx_status(struct dma_chan *dma_chan,
 dma_cookie_t cookie, struct dma_tx_state *state)
{
 struct apple_sio_dma_chan *chan = to_apple_sio_dma_chan(dma_chan);
 struct virt_dma_desc *vd;
 enum dma_status status;
 unsigned long flags;
 status = dma_cookie_status(dma_chan, cookie, state);
 if (status == DMA_COMPLETE || !state) return status;
 spin_lock_irqsave(&chan->vc.lock, flags);
 if (chan->active && chan->active->vd.tx.cookie == cookie)
  dma_set_residue(state, chan->active->length);
 else { vd = vchan_find_desc(&chan->vc, cookie); if (vd) dma_set_residue(state, to_apple_sio_dma_desc(vd)->length); }
 spin_unlock_irqrestore(&chan->vc.lock, flags);
 return status;
}
#endif

static int apple_sio_dma_terminate_all(struct dma_chan *dma_chan)
{
 struct apple_sio_dma_chan *chan = to_apple_sio_dma_chan(dma_chan);
 unsigned long flags;
 LIST_HEAD(head);
 spin_lock_irqsave(&chan->vc.lock, flags);
 chan->poisoned = true; /* Sticky for this diagnostic provider lifetime. */
 vchan_get_all_descriptors(&chan->vc, &head);
 if (chan->active) {
  chan->active->terminated = true;
  apple_sio_dma_queue_finish(chan);
 }
 spin_unlock_irqrestore(&chan->vc.lock, flags);
 /* Only unsent/already-retired objects are on these virtual lists. */
 vchan_dma_desc_free_list(&chan->vc, &head);
 return 0;
}
static void apple_sio_dma_synchronize(struct dma_chan *dma_chan)
{
 struct apple_sio_dma_chan *chan = to_apple_sio_dma_chan(dma_chan);
 flush_work(&chan->issue_work);
 wait_for_completion(&chan->submit_idle);
 flush_work(&chan->finish_work);
 /* A normal finish tail can have queued an issue item; poison stops it. */
 flush_work(&chan->issue_work);
 vchan_synchronize(&chan->vc);
}
static void apple_sio_dma_free_chan_resources(struct dma_chan *dma_chan)
{
 apple_sio_dma_terminate_all(dma_chan);
 apple_sio_dma_synchronize(dma_chan);
 vchan_free_chan_resources(&to_apple_sio_dma_chan(dma_chan)->vc);
}

#ifndef SIO_DMA_MODEL
static int apple_sio_dma_config(struct dma_chan *dma_chan, struct dma_slave_config *config)
{
 if (READ_ONCE(to_apple_sio_dma_chan(dma_chan)->poisoned)) return -ESHUTDOWN;
 return config->direction == DMA_MEM_TO_DEV ? 0 : -EINVAL;
}
static struct dma_chan *apple_sio_dma_of_xlate(struct of_phandle_args *spec, struct of_dma *ofdma)
{
 struct apple_sio_dma *host = ofdma->of_dma_data;
 if (spec->args_count != 1 || spec->args[0] != APPLE_SIO_DMA_TX_ENDPOINT) return NULL;
 return dma_get_slave_channel(&host->tx.vc.chan);
}
static int apple_sio_dma_probe(struct platform_device *pdev)
{
 struct apple_sio **parent_data = dev_get_platdata(&pdev->dev);
 struct apple_sio_dma_chan *chan;
 struct apple_sio_dma *host;
 struct dma_device *dma;
 int ret;
 if (!parent_data || !*parent_data) return -ENODEV;
 host = devm_kzalloc(&pdev->dev, sizeof(*host), GFP_KERNEL);
 if (!host) return -ENOMEM;
 host->dev = &pdev->dev;
 host->sio = *parent_data;
 platform_set_drvdata(pdev, host);
 ret = dma_set_mask_and_coherent(&pdev->dev, DMA_BIT_MASK(64));
 if (ret) return ret;
 dma = &host->dma;
 dma_cap_set(DMA_PRIVATE, dma->cap_mask);
 dma_cap_set(DMA_SLAVE, dma->cap_mask);
 dma->dev = &pdev->dev;
 dma->device_alloc_chan_resources = apple_sio_dma_alloc_chan_resources;
 dma->device_free_chan_resources = apple_sio_dma_free_chan_resources;
 dma->device_prep_slave_sg = apple_sio_dma_prep_slave_sg;
 dma->device_issue_pending = apple_sio_dma_issue_pending;
 dma->device_tx_status = apple_sio_dma_tx_status;
 dma->device_terminate_all = apple_sio_dma_terminate_all;
 dma->device_synchronize = apple_sio_dma_synchronize;
 dma->device_config = apple_sio_dma_config;
 dma->directions = BIT(DMA_MEM_TO_DEV);
 dma->residue_granularity = DMA_RESIDUE_GRANULARITY_DESCRIPTOR;
 dma->dst_addr_widths = BIT(DMA_SLAVE_BUSWIDTH_1_BYTE) | BIT(DMA_SLAVE_BUSWIDTH_2_BYTES) |
                       BIT(DMA_SLAVE_BUSWIDTH_4_BYTES) | BIT(DMA_SLAVE_BUSWIDTH_8_BYTES);
 INIT_LIST_HEAD(&dma->channels);
 chan = &host->tx;
 chan->host = host;
 chan->endpoint = APPLE_SIO_DMA_TX_ENDPOINT;
 INIT_WORK(&chan->issue_work, apple_sio_dma_issue_work);
 INIT_WORK(&chan->finish_work, apple_sio_dma_finish_work);
 init_completion(&chan->submit_idle);
 complete_all(&chan->submit_idle);
 vchan_init(&chan->vc, dma);
 chan->vc.desc_free = apple_sio_dma_desc_free;
 ret = dma_async_device_register(dma);
 if (ret) goto out_vchan;
 ret = of_dma_controller_register(pdev->dev.parent->of_node, apple_sio_dma_of_xlate, host);
 if (ret) { dma_async_device_unregister(dma); goto out_vchan; }
 return 0;
 out_vchan:
 vchan_free_chan_resources(&chan->vc);
 return ret;
}
static void apple_sio_dma_remove(struct platform_device *pdev)
{
 struct apple_sio_dma *host = platform_get_drvdata(pdev);
 of_dma_controller_free(pdev->dev.parent->of_node);
 dma_async_device_unregister(&host->dma);
 apple_sio_dma_free_chan_resources(&host->tx.vc.chan);
}
static struct platform_driver apple_sio_dma_driver = {
 .driver = {.name = "apple-sio-dma"},
 .probe = apple_sio_dma_probe,
 .remove = apple_sio_dma_remove,
};
module_platform_driver(apple_sio_dma_driver);
MODULE_ALIAS("platform:apple-sio-dma");
MODULE_DESCRIPTION("T8010 diagnostic DMA provider with drain-before-release abort work");
MODULE_LICENSE("Dual MIT/GPL");
#endif

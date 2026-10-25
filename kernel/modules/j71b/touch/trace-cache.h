/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
/* Caller serializes this cache. No registers or payload buffers are read here. */
#define SIO_TRACE_CAPACITY 256
struct sio_trace_entry { u64 sequence, ns, message; u8 event; };
struct sio_trace_cache { u64 total; struct sio_trace_entry entries[SIO_TRACE_CAPACITY]; };
static void sio_trace_push(struct sio_trace_cache *cache, u8 event, u64 ns, u64 message)
{
 u64 seq=cache->total++;
 cache->entries[seq % SIO_TRACE_CAPACITY]=(struct sio_trace_entry){
  .sequence=seq,.ns=ns,.message=message,.event=event,
 };
}

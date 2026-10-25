/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
#ifdef SIO_DMA_MODEL
#include <stdint.h>
#include <stdbool.h>
typedef uint64_t u64;
#else
#include <linux/types.h>
#endif
/* Core dispatch already matches the exact endpoint/tag request. Require the
 * known terminal shape and full byte count as well before normal completion.
 */
static inline bool j71b_dma_full_report(u64 response, unsigned long length)
{
 return (response & 0xff) == 26 && ((response >> 8) & 0xff) != 0 &&
        ((response >> 16) & 0xff) == 0x68 &&
        ((response >> 24) & 0xff) == 0x80 &&
        (response >> 32) == length;
}

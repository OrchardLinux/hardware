/* SPDX-License-Identifier: GPL-2.0-only OR MIT */
/* Exact idle termination fence from this T8010 firmware's opcode-8 path.
 * ACK alone is insufficient: firmware queues it before stop/drain processing.
 */
#ifdef IDLE_STOP_MODEL
#include <stdint.h>
#include <stdbool.h>
typedef uint64_t u64;
#else
#include <linux/types.h>
#endif
static inline bool j71b_idle_stop_fence(u64 message)
{
	/* endpoint26, tag0, opcode0x68, parameter0, data0; reject ALL other bits. */
	return message == (26ULL | (0x68ULL << 16));
}
static inline bool j71b_idle_opcode_allowed(unsigned int opcode)
{
	/* No descriptor can ever be submitted through this diagnostic core. */
	return opcode != 0x06;
}

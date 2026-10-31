/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef J72_LIMIT_CORE_H
#define J72_LIMIT_CORE_H
#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/errno.h>
#else
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>
typedef uint64_t u64;
#endif

#define J72_HANDOFF 0x02u
#define J72_TRIAL 0x1cu
#define J72_TRIAL_MS 30000u

struct j72_limit_io {
	void *ctx;
	int (*read)(void *ctx, unsigned int *value);
	int (*write)(void *ctx, unsigned int value);
};

struct j72_limit_state {
	bool used, touched, active, restored, restore_failed;
	u64 deadline_ms;
	int error;
};

/* Caller serializes state. Even a failed write may have reached hardware. */
static int j72_limit_restore(struct j72_limit_state *s,
			     const struct j72_limit_io *io)
{
	unsigned int value, attempt;
	int ret = 0;

	s->active = false;
	if (!s->touched)
		return 0;
	for (attempt = 0; attempt < 3; attempt++) {
		ret = io->write(io->ctx, J72_HANDOFF);
		if (!ret) {
			ret = io->read(io->ctx, &value);
			if (!ret && value == J72_HANDOFF) {
				s->touched = false;
				s->restored = true;
				return 0;
			}
			ret = ret ? ret : -EIO;
		}
	}
	s->restore_failed = true;
	s->error = ret;
	return ret;
}

static int j72_limit_start(struct j72_limit_state *s,
			   const struct j72_limit_io *io, u64 now,
			   int guard_result)
{
	unsigned int value;
	int ret;

	if (s->used || s->active || s->restore_failed)
		return -EPERM;
	s->used = true;
	if (guard_result) {
		s->error = guard_result;
		return guard_result;
	}
	ret = io->read(io->ctx, &value);
	if (ret || value != J72_HANDOFF) {
		s->error = ret ? ret : -EPERM;
		return s->error;
	}
	s->deadline_ms = now + J72_TRIAL_MS;
	s->touched = true;
	s->active = true;
	ret = io->write(io->ctx, J72_TRIAL);
	if (!ret) {
		ret = io->read(io->ctx, &value);
		if (!ret && value != J72_TRIAL)
			ret = -EIO;
	}
	if (ret) {
		s->error = ret;
		j72_limit_restore(s, io);
	}
	return ret;
}

static int j72_limit_check(struct j72_limit_state *s,
			   const struct j72_limit_io *io, u64 now,
			   int guard_result)
{
	unsigned int value;
	int ret;

	if (!s->active)
		return 0;
	if (now >= s->deadline_ms)
		return j72_limit_restore(s, io);
	if (guard_result) {
		s->error = guard_result;
		return j72_limit_restore(s, io);
	}
	ret = io->read(io->ctx, &value);
	if (ret || value != J72_TRIAL) {
		s->error = ret ? ret : -EPERM;
		return j72_limit_restore(s, io);
	}
	return 0;
}

static int j72_limit_pack_guard(int present, int soc, int temp,
			       int voltage, int pack_current)
{
	if (present != 1 || soc < 10 || soc > 95 || temp < 200 || temp > 380 ||
	    voltage < 3500000 || voltage > 4200000 ||
	    pack_current < -1500000 || pack_current > 1500000)
		return -ERANGE;
	return 0;
}
#endif

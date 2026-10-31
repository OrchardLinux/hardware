#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "module/j72-limit-core.h"

struct fake {
	unsigned int value, reads, writes, raised, restored;
	unsigned int fail_read, fail_write;
	bool ambiguous_raise, bad_raise_readback, rollback_broken;
};

static int read_reg(void *ctx, unsigned int *value)
{
	struct fake *f = ctx;
	f->reads++;
	if (f->reads == f->fail_read)
		return -ETIMEDOUT;
	*value = f->value;
	if (f->bad_raise_readback && f->value == J72_TRIAL)
		*value = 0;
	return 0;
}

static int write_reg(void *ctx, unsigned int value)
{
	struct fake *f = ctx;
	assert(value == J72_TRIAL || value == J72_HANDOFF);
	f->writes++;
	if (value == J72_TRIAL) {
		f->raised++;
		if (f->ambiguous_raise) {
			f->value = value;
			return -EIO;
		}
	} else {
		f->restored++;
		if (f->rollback_broken)
			return -ETIMEDOUT;
	}
	if (f->writes == f->fail_write)
		return -EIO;
	f->value = value;
	return 0;
}

static void run_case(unsigned int kind)
{
	struct fake f = {.value = J72_HANDOFF};
	struct j72_limit_state s = {0};
	struct j72_limit_io io = {&f, read_reg, write_reg};
	int ret;

	if (kind == 1) f.value = J72_TRIAL;
	if (kind == 2) f.fail_read = 1;
	if (kind == 3) f.fail_write = 1;
	if (kind == 4) f.ambiguous_raise = true;
	if (kind == 5) f.fail_read = 2;
	if (kind == 6) f.bad_raise_readback = true;
	ret = j72_limit_start(&s, &io, 1000, kind == 7 ? -ERANGE : 0);
	if (kind >= 1 && kind <= 7) {
		assert(ret < 0 && !s.active);
		if (kind == 1 || kind == 2 || kind == 7)
			assert(f.writes == 0);
		else
			assert(f.value == J72_HANDOFF && s.restored);
	} else {
		assert(ret == 0 && s.active && f.value == J72_TRIAL);
		if (kind == 8) f.fail_read = 3;
		if (kind == 9) f.value = 0x3e;
		if (kind == 10) f.rollback_broken = true;
		if (kind == 11) f.fail_write = 2;
		if (kind == 12) f.fail_read = 3;
		ret = j72_limit_check(&s, &io,
			(kind == 0 || kind == 10 || kind == 11 || kind == 12) ? 31000 : 2000,
			kind == 13 ? -ERANGE : 0);
		if (kind == 10) {
			assert(ret < 0 && s.restore_failed && !s.restored);
			assert(f.restored == 3 && s.touched);
		} else if (kind == 14) {
			assert(s.active && f.value == J72_TRIAL && !s.restored);
			assert(j72_limit_restore(&s, &io) == 0);
		} else {
			assert(ret == 0 && s.restored && f.value == J72_HANDOFF);
		}
		assert(!s.active);
	}
	assert(j72_limit_start(&s, &io, 50000, 0) == -EPERM);
	assert(f.raised <= 1);
}

int main(void)
{
	unsigned int kind;
	for (kind = 0; kind < 15; kind++) run_case(kind);
	assert(j72_limit_pack_guard(1, 87, 345, 4050000, -743000) == 0);
	assert(j72_limit_pack_guard(0, 87, 345, 4050000, -743000) < 0);
	assert(j72_limit_pack_guard(1, 96, 345, 4050000, -743000) < 0);
	assert(j72_limit_pack_guard(1, 9, 345, 4050000, -743000) < 0);
	assert(j72_limit_pack_guard(1, 87, 199, 4050000, -743000) < 0);
	assert(j72_limit_pack_guard(1, 87, 381, 4050000, -743000) < 0);
	assert(j72_limit_pack_guard(1, 87, 345, 3499999, -743000) < 0);
	assert(j72_limit_pack_guard(1, 87, 345, 4200001, -743000) < 0);
	assert(j72_limit_pack_guard(1, 87, 345, 4050000, -1500001) < 0);
	assert(j72_limit_pack_guard(1, 87, 345, 4050000, 1500001) < 0);
	puts("15 actual policy cases + 10 pack-guard cases passed");
	return 0;
}

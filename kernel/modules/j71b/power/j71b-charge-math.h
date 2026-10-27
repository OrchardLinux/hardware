/* SPDX-License-Identifier: GPL-2.0-only */
/* D2365 arithmetic adapted from linux-a9 charge7, reconstructed from iOS. */
#ifndef J71B_CHARGE_MATH_H
#define J71B_CHARGE_MATH_H

static const int source_steps[] = { 500000, 1000000, 2100000, 2400000 };

static int source_step(int ua)
{
	int i;

	for (i = ARRAY_SIZE(source_steps) - 1; i >= 0; i--)
		if (ua >= source_steps[i])
			return i;
	return -1;
}

static int divider_current(int dp_mv, int dm_mv)
{
	/* The sixth iOS entry is 3000 mA, bounded later by the board ceiling. */
	static const int limits[] = {
		500, 1000, 1000, 2100, 2400, 3000, 1000, 1000, 1000,
	};
	int dp, dm;

	if (dp_mv < 0 || dm_mv < 0 || dp_mv > 4000 || dm_mv > 4000)
		return -ERANGE;
	if (dp_mv < 1500 || dm_mv < 1500)
		return 0;
	dp = dp_mv >= 2880 ? 2 : dp_mv >= 2200 ? 1 : 0;
	dm = dm_mv >= 2880 ? 2 : dm_mv >= 2200 ? 1 : 0;
	return limits[3 * dp + dm] * 1000;
}

/* Validate this unit's 24-byte input-limit record; never use J71t's bytes.
 * J71b 21H461 calibration branches: fffffff006b857a0 (middle/high ranges).
 */
static int calibrate_input(const u8 *data, size_t size, int target_ma, u8 *code)
{
	s64 point[5], slope, gain, result, ma;
	int i;

	if (!data || size != 24 || target_ma < 500 || target_ma > 2400 ||
	    get_unaligned_le32(data + 20) != 1)
		return -EINVAL;
	for (i = 0; i < 5; i++) {
		point[i] = (s32)get_unaligned_le32(data + i * 4);
		if (point[i] <= 0 || point[i] > (3000LL << 16) ||
		    (i && point[i] <= point[i - 1]))
			return -ERANGE;
	}
	if (target_ma < 1500) {
		slope = div_s64(point[2] - point[1], 53);
		gain = div_s64(point[1] - point[2] + (1025LL << 16), 525);
		result = ((gain * ((target_ma - 475LL) * 65536)) >> 16) -
			 point[1] + (925LL << 16);
	} else {
		slope = div_s64(point[4] - point[3], 30);
		gain = div_s64(point[3] - point[4] + (600LL << 16), 300);
		result = ((gain * ((target_ma - 2100LL) * 65536)) >> 16) -
			 point[3] + (4125LL << 16);
	}
	if (slope < (8LL << 16) || slope > (12LL << 16))
		return -ERANGE;
	ma = result >> 16;
	if (ma < 75 || ma > target_ma)
		return -ERANGE;
	*code = div_s64((ma * 8 - 600) & 0xfff8, 100);
	return *code <= 0xba ? 0 : -ERANGE;
}
#endif

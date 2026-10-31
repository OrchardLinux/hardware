/* Generated from the exact kernel source; see profile-source.json. */
enum bq27xxx_reg_index {
	BQ27XXX_REG_CTRL = 0,	/* Control */
	BQ27XXX_REG_TEMP,	/* Temperature */
	BQ27XXX_REG_INT_TEMP,	/* Internal Temperature */
	BQ27XXX_REG_VOLT,	/* Voltage */
	BQ27XXX_REG_AI,		/* Average Current */
	BQ27XXX_REG_FLAGS,	/* Flags */
	BQ27XXX_REG_TTE,	/* Time-to-Empty */
	BQ27XXX_REG_TTF,	/* Time-to-Full */
	BQ27XXX_REG_TTES,	/* Time-to-Empty Standby */
	BQ27XXX_REG_TTECP,	/* Time-to-Empty at Constant Power */
	BQ27XXX_REG_NAC,	/* Nominal Available Capacity */
	BQ27XXX_REG_RC,		/* Remaining Capacity */
	BQ27XXX_REG_FCC,	/* Full Charge Capacity */
	BQ27XXX_REG_CYCT,	/* Cycle Count */
	BQ27XXX_REG_AE,		/* Available Energy */
	BQ27XXX_REG_SOC,	/* State-of-Charge */
	BQ27XXX_REG_DCAP,	/* Design Capacity */
	BQ27XXX_REG_AP,		/* Average Power */
	BQ27XXX_DM_CTRL,	/* Block Data Control */
	BQ27XXX_DM_CLASS,	/* Data Class */
	BQ27XXX_DM_BLOCK,	/* Data Block */
	BQ27XXX_DM_DATA,	/* Block Data */
	BQ27XXX_DM_CKSUM,	/* Block Data Checksum */
	BQ27XXX_REG_SEDVF,	/* End-of-discharge Voltage */
	BQ27XXX_REG_PKCFG,	/* Pack Configuration */
	BQ27XXX_REG_MAX,	/* sentinel */
};
#define INVALID_REG_ADDR 0xff
#define BQ27XXX_DM_REG_ROWS \
	[BQ27XXX_DM_CTRL] = 0x61,  \
	[BQ27XXX_DM_CLASS] = 0x3e, \
	[BQ27XXX_DM_BLOCK] = 0x3f, \
	[BQ27XXX_DM_DATA] = 0x40,  \
	[BQ27XXX_DM_CKSUM] = 0x60
static const u8 j208_expected_regs[BQ27XXX_REG_MAX] = {
		[BQ27XXX_REG_CTRL] = 0x00,
		[BQ27XXX_REG_TEMP] = 0x06,
		[BQ27XXX_REG_INT_TEMP] = 0x28,
		[BQ27XXX_REG_VOLT] = 0x08,
		[BQ27XXX_REG_AI] = 0x14,
		[BQ27XXX_REG_FLAGS] = 0x0a,
		[BQ27XXX_REG_TTE] = 0x16,
		[BQ27XXX_REG_TTF] = INVALID_REG_ADDR,
		[BQ27XXX_REG_TTES] = INVALID_REG_ADDR,
		[BQ27XXX_REG_TTECP] = INVALID_REG_ADDR,
		[BQ27XXX_REG_NAC] = 0x0c,
		[BQ27XXX_REG_RC] = 0x10,
		[BQ27XXX_REG_FCC] = 0x12,
		[BQ27XXX_REG_CYCT] = 0x2a,
		[BQ27XXX_REG_AE] = INVALID_REG_ADDR,
		[BQ27XXX_REG_SOC] = 0x2c,
		[BQ27XXX_REG_DCAP] = 0x3c,
		[BQ27XXX_REG_AP] = 0x24,
		BQ27XXX_DM_REG_ROWS,
	};

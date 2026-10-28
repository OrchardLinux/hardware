# CBTL1610 ("Tristar") Lightning port controller

J71b: `tristar,cbtl1610` on i2c0 `0x1a`. The ADT also lists an interrupt on
AP GPIO28 (flags 3) and the function bindings `function-brick_id_voltage`
(`adcV`) and `function-power_supply_change` (`Iump`). iPadOS class
`AppleCBTL1610` derives from `AppleTHS7383` and overrides some methods.

Plain I²C register reads (write register, then read) worked from Linux.
PR #8 claims iPadOS issues the register selector with a STOP and then a
separate read START; Paul's Bellatrix driver does the same on its chip.
Orchard's plain combined transfer worked on J71b.

## Registers used

| Reg | Access | Meaning | Evidence |
|---|---|---|---|
| `0x01` | R/W | Route / analog mux byte. Two nibbles, one per Lightning lane pair. Idle value 0 on J71b | Firmware, hardware |
| `0x03` | R/W | Excitation for port-type (BC1.2-style) modes 4/5. Idle 0. Must read 0 after every sample | Firmware, hardware |
| `0x0c` | R | Status. Bit 7 = valid, bit 6 = orientation (0 → 1, 1 → 2), bit 2 = absent. Observed `0xa3` attached, `0x24` detached | Firmware (`…615c498`, presence `…615c440`), hardware |
| `0x20`–`0x27` | R | Cached identification response: opcode `0x75`, six data bytes, CRC-8 | Firmware (CBTL1610 override `…6154e68`), hardware |
| `0x0a`, `0x0d` | R | Uninterpreted (`0x0d` once `0x1d`, later 0). Do not read to explore; may have side effects | Hardware |

PR #8 claims, not verified by Orchard:

- `0x0c` bits 1:0 are VBUS indications (both set = present). This is
  consistent with `0xa3` and `0x24`.
- `0x5d`–`0x61` are a TX/command engine. Writing request `0x74` with host ID
  `0x0002` makes the controller fetch a fresh response, which arrives as an
  8-byte `0x75` frame.
- Only Tristar revision `0x8a` was qualified.

Orchard read only the cached frame and never issued a request.

## Capability frame

```
s1 = read(0x0c); require (s1 & 0x84) == 0x80
p  = read8(0x20)            # p[0] = 0x75, p[1..6] data, p[7] CRC
s2 = read(0x0c); require s2 == s1
require crc8(p[0..6]) == p[7]   # init 0xff, reflected poly 0x8c, LSB first, no final XOR
orientation = 1 + ((s2 >> 6) & 1)
```

CRC helper: CBTL1610 calls stub `…615dec8`, which resolves via GOT
`…6e551e8` to `…5ed7638`. Direction argument 2 traverses the bytes forward.

Decode (firmware `…5ef32c4` with helper `…5ef2acc`). Use data bytes
d0 = `p[1] & 0x3f`, d2 = `p[3] & 0x08`, d3 = `p[4] & 0x70`:

| Bit | Function |
|---|---|
| d0 & `0x18` | USB |
| d0 & `0x03` | UART |
| d0 & `0x04` | MBUS (enables the special combinations below) |
| d2 & `0x08` | USB-C |
| d0 & `0x20` | Extended layout. Rejected, not decoded |

With MBUS set, the helper can replace the legacy flags:

| Condition | Result |
|---|---|
| USB, `(d0 & 3) == 1`, `d3 == 0x20` | USB only |
| USB-C, USB, `(d0 & 3) == 2` | USB + UART2 |
| USB-C, `(d0 & 0x1b) == 0x1b` | MBUS + UART2 |
| USB-C, `(d0 & 0x1b) == 0x0b` | UART0 + UART2 |

Three or more resulting functions mean the route fails. The tags are
ordered `[first, second]` for orientation 1 and swapped for orientation 2.
For example, a USB-C cable gives `usb0/usbc` in orientation 1 and
`usbc/usb0` in orientation 2.

Measured: Lightning to USB-A gave `usb0/none`; Lightning to USB-C gave
`usb0/usbc` (both CRC-valid, J71b).

iPadOS also compares the data bytes to a fixed profile
`18 fe 09 a1 00 00` (`…5ebc258`–`…5ebc2b0`) as one Type-C eligibility
gate. Orchard exports only whether the frame matched, never the frame
itself. PR #8 claims the Apple USB-C to Lightning cable answers
`10 09 08 00 00 00`.

Privacy: the frame is cable identification data. Keep it in kernel memory,
wipe it after decoding, and log only the decoded tags.

## Mux selections

Each measurement writes `0x01` (and, for modes 4/5, `0x03`), waits, starts
an ADC channel 1 conversion, reads the result, then restores `0x01` to the
cached value and verifies `0x03` = 0. If restoration cannot be verified,
stop sampling and hold the default limit until reboot.

### USB-A divider (guarded monitor `…615d57c`)

The monitor rejects the cached route unless `(route & 7) == 1` or
`(route & 0x70) == 0x10`. That is why a route byte is written first.

| Bank | Route | D+ select (mode 1) | D− select (mode 2) | Settle |
|---|---|---|---|---|
| 0 | `0x11` | `0x1c` | `0x1d` | 5 ms |
| 1 | `0x10` | `0xc0` | `0xd0` | 5 ms |

Mode 1 = D+ (primary) and mode 2 = D− (secondary). Firmware republishes
them in that order as `d+ voltage` / `d- voltage` (`…5e69b78`, `…5e69d7c`).

### Port-type modes 4/5 (same monitor, BC1.2-style)

| Route | Mode 4 | Mode 5 |
|---|---|---|
| `0x11` | `0x01`=`0x1d`, `0x03`=`0x09` | `0x01`=`0x1c`, `0x03`=`0x0a` |
| `0x10` | `0x01`=`0xd0`, `0x03`=`0x0b` | `0x01`=`0xc0`, `0x03`=`0x0c` |

Sequence: 5 ms after `0x01`, 40 ms after `0x03`, ADC, clear `0x03`, then
restore `0x01` (`…615d758`–`…615d778`). One J71b attempt on the 140 W USB-C
charger lost the source (`0x18a` `0x42` → `0x00`, events 72/73/76/86) about
20 ms into the first bank, before any ADC. Whether the excitation caused
that is unknown. See [CH-04](experiments.md#ch-04).

### USB-C CC (direct monitor `…615da44`)

Selectors 2–5 select one nibble of `0x01`. Packed constants are
`0xd00dc00c` and `0x0ff00ff0`:

| Controller selector | `0x01` value |
|---|---|
| 2 | `(cached & 0xf0) \| 0x0c` |
| 3 | `(cached & 0x0f) \| 0xc0` |
| 4 | `(cached & 0xf0) \| 0x0d` |
| 5 | `(cached & 0x0f) \| 0xd0` |

The Type-C task (`…5ebbc80`) picks public selector 0 for orientation 2 and
2 otherwise. Table `…5531990` translates public 0 → controller 2 (`0x0c`)
and public 2 → controller 3 (`0xc0`). Settle 5 ms, read ADC channel 1,
restore. There is no route precondition and no `0x03` write.

The CBTL1610 override at vtable slot `0x628` (`…61552f4`) special-cases
selectors 0, 1 and `0xffff`, and delegates 2/3 via `…6e5a9b0`+`0x638` to
`…615da44`.

## Vendor routing (why direct route writes are a hypothesis)

The real route handler `…615c57c` does more than write `0x01`:

- It reads six bytes of `IOAccessoryDigitalID` from the BuiltIn provider
  (slot `0x628` → `…5ecf154`) and the `IOAccessoryOrientation` property
  (stub `…615dfc4` → `…5ef2b68`).
- It combines them with route tags and the cached byte, and may reject or
  alter the result.
- It may call provider functions `function-usb_hs_enable`,
  `function-usb_hs_disable` and `function-usb_cc_select`. None of these
  exist in the J71b ADT, nor do `function-eis_afe_en` or
  `function-ldcm_bypass_en`.
- It may write an intermediate transition byte before the final one.
- It updates the cached route (object + `0x100`) only after a successful
  final write.

Route tags map roughly as follows. First tag: `usb0` → `01`, `urt0` → `02`,
`usbc`/`mbus` → `03`, `usb1` → `06`, `jtag` → `07`. Second tag adds
`usb0` → `10`, `urt0` → `20`, `usbc`/`mbus` → `30`, `urt2` → `60`,
`urt1` → `70`. A narrow capability layout with byte 0 bit 4 set turns
`31`/`13` into `b1`/`1b`.

Linux writes `0x11`/`0x10` directly from an idle 0. This produced correct,
orientation-tolerant signatures on every tested source. It is still not a
replay of the vendor sequence. Verify on other boards rather than copy.

## Dispatch map (21H461, iPad7,5/7,6)

| Slot | CBTL1610 (vtable `…6e58c18`, ctor `…61545a4`) | THS7383 base (vtable `…6e5a9c0`, ctor `…615be80`) |
|---|---|---|
| `0x5e0` response | `…6154e68` (8 bytes at `0x20`, forward, CRC) | `…615c2f0` (7 bytes at `0x39`, reversed) |
| `0x5f0` orientation | `…615c498` (inherited) | `…615c498` |
| `0x628` monitor | `…61552f4` | `…615da44` |
| `0x5c0` event | `…6155518` | `…615d05c` |

Other anchors:

- guarded monitor `…615d57c`; route handler `…615c57c`;
- `readBrickID` callback `…6152e64`;
- reached through instance vtable slots: `+0x630` → `…61567f8` → table
  `…6e5aff0` → monitor, and `+0x600` → `…6155070` → table `…6e5afc0` →
  route handler.

The routine near `…6159c60` that writes `0x81`/`0x82`/`0x83` belongs to a
different table and is not J71b init.

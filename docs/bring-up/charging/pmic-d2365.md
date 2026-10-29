# D2365 ("Angel") charger and ADC registers

J71b: `apple,angel-pmic` on i2c0 `0x3c` (Linux `0-003c`), 16-bit register
addresses via regmap. Addresses below are from the iPadOS 17.7.11 (21H461)
`com.apple.driver.AppleD2365PMU` kext (`__TEXT_EXEC` at `0xfffffff006b7e000`).
Use the corrected disassembly; the first listing was shifted by +0x80.

## Charger registers

| Register | Meaning | Evidence |
|---|---|---|
| `0x18a` | Charge/input status, read without acknowledging. J71b values: `0x42` with any source, `0x00` unplugged, `0x40` for about 0.1 s during reattach before `0x42` | Hardware |
| `0xa00` | Input enable/hold bits. iPadOS clears and sets bit 1 around a limit change (`…6b8507c`). Linux does not need it; Paul's driver checks it reads `0x00` | Firmware, hardware |
| `0xa03` | USB input-current limit code, one byte (read `…6b84f40`, write `…6b84f94`) | Firmware, hardware |
| `0xa08` | Pack charge-current limit, low 6 bits × 50 mA (`…6b84dfc`) | Firmware |
| `0xa0c` | Pack charge-current cap, low 6 bits × 50 mA (`…6b84948`). J71b reads `0xf4` (field `0x34` = 2600 mA) | Firmware, hardware |
| `0xa14` | Charge-current setting readback (`…6b84eb4`) | Firmware |
| `0x4000` | ADC channel select. A write starts a conversion | Firmware (`…6b85e0c`), hardware |
| `0x4001`–`0x4002` | ADC result: byte 0 bit 7 = error, bits 3:0 = low nibble; byte 1 = high 8 bits (12-bit result) | Firmware (`…6b85e40`), hardware |
| `0x140`–`0x15d` | 30-byte PMU event block, read-to-clear, shared by all PMU clients | [pmu-events.md](pmu-events.md) |
| `0x1c0`–`0x1dd` | 30-byte PMU event mask block. Not written by any Orchard code | Firmware, hardware read |

Paul Praschl's driver describes the same block as `status` at `0x180`
(length `0x1e`) and `control` at `0xa00` (length `0x0d`). It decodes `0x18a` as:

- bits 1:0: charge state (iPadOS "IsCharging" when nonzero);
- bit 2: charge end;
- bit 4: charge timeout.

On J172 the charge-state field can stay set while Bellatrix reports no
input. On J71b, `0x18a` always went to `0x00` on unplug in our tests.
Treat `0x18a` as one input to presence, not a source identity. It does not
change on a quick swap (see [pmu-events.md](pmu-events.md)).

Earlier Orchard code's "online" rule for `0x18a`: bit 4 clear, bits 1:0
nonzero, high bits = `0x40`. The prototype requires exactly `0x42` to
identify and treats `0x40` as transitional (no grant).

## Input-limit encoding

Nominal: **mA = code × 12.5 + 75**. Linux, rounding down:
`code = (mA − 75) × 2 / 25`.

| `0xa03` | Nominal mA | Status |
|---|---|---|
| `0x00` | 75 | Formula floor; iPadOS rejects calibrated targets below 75 |
| `0x02` | 100 | Handoff from iBoot; restore value |
| `0x12` | 300 | Hardware trial |
| `0x1c` | 425 | Project default for unidentified sources |
| `0x32` | 700 | Hardware trial, known wall source |
| `0x4a` | 1000 | Hardware trial |
| `0x6c` | 1425 | Paul's J172 grant (not tested on J71b) |
| `0x72` | 1500 | Hardware trial |
| `0x9a` | 2000 | Hardware trial |
| `0xa2` | 2100 | Derived only |
| `0xba` | 2400 | Hardware trial; J71b board maximum |

The fit is exact at every programmed code. The pack responded monotonically
(see [measurements.md](measurements.md)). The actual input current was never
measured.

iPadOS does not use the formula. It builds a calibration object from the ADT
property `usb-input-limit-calibration` (`…6b84970` clears it, `…6b84980`
re-reads the property, `…6b84fb0` calls its `vtable[0x40]` with the target in
Q16.16). It then compares the result against 75 (`…6b8501c`) and writes one
byte.

PR #8 (Berkay Kösebay, claim, J71b-unverified) reconstructs that object:

- The property is 24 bytes: five signed little-endian Q16 points plus version 1.
- There are separate low/high-range interpolations: below 1500 mA they use
  points 1–2, above they use points 3–4.
- Slopes must be between 8 and 12 (Q16).

The calibration is per unit. Never copy one device's bytes to another, and
never publish them.

## Pack charge current

`mA = (value & 0x3f) × 50`; write `code = min(mA, 3150) / 50`. `0xa08` is
clamped by `0xa0c`. Paul's J172 config and J71b both carry the cap field
`0x34`. Do not change the pack limit to get more input current. They are
separate quantities.

PR #8 writes `0xa08` (pack) before `0xa03` (input) when raising, and restores
the saved `0xa08` on handoff. That is a claim and has not been verified on J71b.

## ADC

| Step | Detail |
|---|---|
| Channel | 1 = `function-brick_id_voltage` (ADT: `adcV`, Q16.16 gain 65536). The same channel carries Tristar mux output for D+/D−/CC |
| Start | write channel to `0x4000` |
| Completion | PMU event 176 (firmware: encoded `0x1601`, registered at `…6b7e414`–`…6b7e428`, callback `…6b7c3b4`; hardware: one conversion asserted GPIO38 with exactly 176 new) |
| Result | read 2 bytes at `0x4001`; reject byte 0 bit 7 |
| Conversion | `mV = ((raw × 5000 × 16 + 40 × 65536) / 1000) × 1000 / 65536`, about raw × 1.2207 + 40; raw 0 gives 39 mV |

iPadOS serializes conversions (`…6b7c3f4`): it sets a state to 1, starts the
conversion, and waits for the completion callback to set it to 2.

A fixed 50 ms delay worked in early diagnostics, but it is not a completion
signal. PR #8 instead polls bit 0 of PMIC `0x156` for "ready". `0x156` is
byte 22 of the event block, and its bit 0 is event 176, so PR #8 polls the
completion event directly. Whether a one-byte read clears only that byte or
more of the shared block is not known. If it clears more, it acknowledges
other clients' events. This has not been verified on J71b.

Observed hardware timing: about 8.4 ms from the select log line to the
GPIO38 assertion. That includes logging and the I²C write, so it is not the
conversion time.

## Handoff and start conditions

| Read at start | J71b value | Use |
|---|---|---|
| `0xa03` | `0x02` (or `0x1c` if an earlier Orchard owner ran) | Refuse to take ownership on anything else |
| `0xa0c` | `0xf4` | Refuse if different |
| `0x18a` | `0x42` / `0x00` | Present / absent |

## Not established

- The meaning of the other `0x180` status bytes.
- Whether the PMIC limits input on its own (AICL). PR #8 claims, from
  J71b firmware, that the D2365 input-limiting callback is constant false
  (`…6b85348`) and measured input current is unsupported (`…6b85230`).
  That fits the absence of any VBUS/IBUS reading in iPadOS's D2365 path,
  but Orchard has not checked it.
- Whether attaching a source resets `0xa03`. VBUS loss does not
  ([CH-09](experiments.md#ch-09)).

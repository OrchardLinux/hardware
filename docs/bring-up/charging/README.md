# Charging bring-up

What Orchard learned bringing up charging on the iPad 6 Wi-Fi (J71b, T8010,
Dialog D2365 "Angel" PMIC, NXP CBTL1610 "Tristar" Lightning port controller),
how it was found, and how to do the same on another board faster. Open
questions are written as runnable experiments in [experiments.md](experiments.md),
with a per-device menu.

Reference files:

| File | Contents |
|---|---|
| [pmic-d2365.md](pmic-d2365.md) | Charger registers, input-limit encoding, ADC, status byte `0x18a`, calibration |
| [pmu-events.md](pmu-events.md) | Shared event block `0x140`, GPIO38, event attribution, withdrawal timings |
| [tristar-cbtl1610.md](tristar-cbtl1610.md) | Tristar registers, capability frame, mux selections, vendor dispatch map |
| [brickid.md](brickid.md) | Apple USB-A divider table, port-type (BC1.2) table, measured signatures |
| [usb-c.md](usb-c.md) | USB-C CC measurement, thresholds, iPadOS policy caps, Rp changes, PD lead |
| [ios-firmware.md](ios-firmware.md) | How the iPadOS kernelcache was read, address anchors, iOS runtime observation |
| [measurements.md](measurements.md) | Summarized hardware logs: current trials, signatures, events, latencies |
| [experiments.md](experiments.md) | Open experiments and the per-device menu |

Firmware addresses are unslid VAs in the iPadOS 17.7.11 (21H461) iPad7,5/7,6
kernelcache; `…5e69b38` abbreviates `0xfffffff005e69b38`.

## Status (2026-10-28)

| Piece | J71b state | Evidence |
|---|---|---|
| Battery gauge (BQ27540 over HDQ) | Works through `power_supply` | Hardware, many boots |
| Conservative charging, 425 mA | Shipped in the old stack (userspace lease, removed in the stack change) | Hardware |
| Input-limit programming 100–2400 mA | Works, readback verified | Manual trials on known sources |
| Cable type (USB-A vs USB-C) | Works | Capability frame, hardware |
| USB-A Apple charger current | Works, both orientations | ID4 and ID5 sources, hardware |
| USB-C Rp current (1.5 A / 3 A) | Works on demand | 140 W wall, power bank, Mac, hardware |
| Withdrawal on unplug or source change | Works, <1 ms edge to verified write | Prototype owner, hardware |
| Prototype that grants identified current | Hardware-tested in one RAM session | hardware commit `11073db` |
| In-tree driver `apple_tristar_charger` | Written, built in; hardware test pending | [Linux driver](#5-linux-driver) |
| Computers / generic chargers (BC1.2, CDP) | Not implemented, held at 425 mA | [CH-04](experiments.md#ch-04) |
| USB PD | Not implemented | [CH-05](experiments.md#ch-05) |
| Suspend | Not tested with charging | [CH-06](experiments.md#ch-06) |
| Live USB-C Rp changes without detach | Not detected (CC is sampled on demand) | [CH-07](experiments.md#ch-07) |

Evidence vocabulary used in this directory:

- **Hardware**: measured on a J71b, with a record in the research archive.
- **Firmware**: read from the iPadOS 17.7.11 (21H461) kernelcache for iPad7,5/7,6.
  Shows what iPadOS does, not that Linux does the same.
- **iOS observation**: read from the IORegistry of a running iPadOS.
- **Claim (PR #8 / linux-a9)**: from Berkay Kösebay's iPad 5 work. Not verified by Orchard.
- **Inferred**: our reasoning, not measured.

## 1. How charging works on these devices

```
 source ── Lightning ── CBTL1610 (i2c0 0x1a) ── mux: D+/D-/CC lanes ──┐
                              │                                      │ analog
                              │ capability frame, orientation        ▼
                              │                          D2365 ADC channel 1 (0x4000/0x4001)
 VBUS ──────────────────────────────────────────────▶ D2365 charger (i2c0 0x3c)
                                                         input limit 0xa03 ── battery
                                                         event block 0x140 ── GPIO38 (AP)
 BQ27540 gauge ── HDQ on UART5 ── AP
```

The PMIC limits how much current the board draws from VBUS. Nothing in the
hardware negotiates that limit with the source. Software must:

1. find out what the attached source can supply;
2. program the PMIC input limit to at most that, and at most the board maximum;
3. drop the limit again as soon as the source may have changed.

If step 3 is late, a high limit set for a 2.4 A charger stays in place when
the cable moves to a 500 mA computer port. The board then tries to draw more
than the port can deliver.

### Components on J71b

| Component | Where | Role | Evidence |
|---|---|---|---|
| D2365 PMIC (`apple,angel-pmic`) | i2c0 `0x3c` | Input limit, pack charge current, ADC, PMU events | ADT, hardware |
| CBTL1610 (`tristar,cbtl1610`) | i2c0 `0x1a` | Lightning routing, cable capability, analog mux to the PMIC ADC | ADT, firmware, hardware |
| Charger profile (`charger,k48`) | ADT node, no separate chip | Board policy: `usb-input-limit-max` = 2400 mA, `min-charge-capable-current` = 1000 mA, `usb-input-limit-calibration`, `charge-currents`, `charge-limits`, `boot-adapter-id`, `charger-curve` | ADT |
| BQ27540 gauge | HDQ on UART5, AP pin 173 | Pack voltage, current, temperature, capacity, cycles | ADT, hardware |
| PMU interrupt | AP GPIO38 | Shared PMIC event line, asserted low | ADT, hardware |
| Tristar interrupt | AP GPIO28 (ADT flags 3) | No edge seen in our tests; unused | ADT, hardware |

### The input-current limit

| Fact | Value | Evidence | Record |
|---|---|---|---|
| Register | D2365 `0xa03`, one byte | Firmware (`AppleD2365PMU` read `…6b84f40`, write `…6b84f94`), hardware readback | [pmic-d2365.md](pmic-d2365.md) |
| Encoding | mA = code × 12.5 + 75 | Fits all six programmed codes and the 2400 mA ceiling; firmware compares the calibrated target against 75 before writing | [pmic-d2365.md](pmic-d2365.md) |
| Handoff value | `0x02` = 100 mA | Observed at Linux start; every owner refused to start on any other unexplained value | [measurements.md](measurements.md) |
| Project default | `0x1c` = 425 mA | Chosen, see below | — |
| Board maximum | `0xba` = 2400 mA | ADT `usb-input-limit-max`; manual trials at 2400 mA | [measurements.md](measurements.md) |
| VBUS loss does not reset it | `0x1c` stayed for 4.9 s after unplug, until software restored `0x02` | Hardware | [pmu-events.md](pmu-events.md) |
| Pack charge current is separate | `0xa08` (limit) and `0xa0c` (cap), 50 mA steps, J71b cap field `0x34` = 2600 mA | Firmware, hardware read | [pmic-d2365.md](pmic-d2365.md) |

Why 425 mA: USB 2.0 allows a configured device 500 mA. 425 mA is that minus
a 15 % margin, and it is the highest value we hold without knowing the source.
It is a project choice, not something the hardware or iPadOS defines. A
computer port that only allows 100 mA (unconfigured) is not protected by it.
The handoff value `0x02` = 100 mA equals one USB 2.0 unit load. That iBoot
chose it for this reason is our inference.

The linear formula is a nominal map. iPadOS does not use it: it passes the
target through a per-unit calibration object built from the ADT property
`usb-input-limit-calibration` (log string `inputLimitTargetMA=%d
calibrated_targetMA=%d`). The real input current per code was never measured
with a meter ([CH-14](experiments.md#ch-14)). `0xa2` (2100 mA) is derived, not tested.

## 2. Source identification

Identification has three steps on J71b. Each one reads the Tristar or the
PMIC ADC. None of them changes the input limit.

### 2.1 Cable type: the CBTL1610 capability frame

The Lightning cable announces what it carries. The CBTL1610 caches the cable's
identification response. Linux reads it without sending any request:

1. read status `0x0c`; require bit 7 set (valid) and bit 2 clear (present);
2. read 8 bytes at `0x20`: opcode `0x75`, six data bytes, CRC-8;
3. read `0x0c` again; require it unchanged;
4. check CRC-8 (init `0xff`, reflected polynomial `0x8c`, no final XOR) over
   the first 7 bytes against byte 7;
5. decode route tags from data bytes 0, 2 and 3 (see
   [tristar-cbtl1610.md](tristar-cbtl1610.md#capability-frame)).

| Cable | Decoded route tags | Evidence |
|---|---|---|
| Lightning to USB-A (Mac/hub) | `usb0` / none | Hardware, CRC valid |
| Lightning to USB-C (power bank) | `usb0` / `usbc` | Hardware, CRC valid |

A `usbc` tag means the cable carries CC, so Type-C current applies. No `usbc`
tag means USB-A, so look for an Apple divider. Orientation is status `0x0c`
bit 6 (clear = 1, set = 2). Whether bit 6 follows a physical flip is unresolved;
see [usb-c.md](usb-c.md#orientation) and [CH-10](experiments.md#ch-10).

Trap: the first reader used the THS7383 base-class layout (7 bytes at
`0x39`, reversed) and failed. CBTL1610 overrides it with 8 bytes at `0x20`,
forward, with CRC. Check the concrete class's vtable, not the base class.

### 2.2 USB-C: CC level on the orientation-selected lane

| Step | Detail | Evidence |
|---|---|---|
| Select lane | Tristar reg `0x01` = `0xc0` (orientation 1) or `0x0c` (orientation 2); preserve the other nibble if nonzero | Firmware (direct monitor `…615da44`) |
| Settle | 5 ms | Firmware |
| Convert | PMIC ADC channel 1; completion is PMU event 176 | Firmware, hardware |
| Restore | reg `0x01` back to the cached value (0); verify reg `0x03` = 0 | Hardware |
| Classify | < 660 mV: default USB power; 660–1229 mV: 1.5 A; ≥ 1230 mV: 3 A | Firmware (`…5e69690`) |

| Source (same Apple USB-C to Lightning cable) | CC mV | Class |
|---|---|---|
| Apple 140 W USB-C charger | 1741 (other lane 872) | 3 A |
| 20 W power bank, USB-C port | 1766–1792 | 3 A |
| Mac USB-C port | 961 | 1.5 A |

The prototype granted Type-C sources min(advertised, 2400 mA). iPadOS
applies more policy. It converts 3 A to 1.5 A when the previously detected
charging-port type is 0. It selected 1500 mA on the same 140 W charger and
2400 mA on the power bank ([usb-c.md](usb-c.md#ipados-policy)). That gap is
open ([CH-12](experiments.md#ch-12)).

USB-C rule from the Type-C specification: a sink must follow an Rp change
within tSinkAdj = 60 ms. The CC level is only visible when the mux is
switched to it. Nothing interrupts on an Rp change, as far as we know. See
[CH-07](experiments.md#ch-07).

### 2.3 USB-A: Apple BrickID divider on both mux banks

Apple chargers put fixed voltages on D+ and D−. iPadOS maps the pair to a
BrickID and a current (firmware `…5e69b38`, table `…549bf00`):

| D+ \ D− | 1500–2183 mV | 2184–2879 mV | ≥ 2880 mV |
|---|---|---|---|
| 1500–2183 mV | ID1: 500 mA | ID2: 1000 mA | ID3: 1000 mA |
| 2184–2879 mV | ID4: 2100 mA | ID5: 2400 mA | ID6: 3000 mA |
| ≥ 2880 mV | ID7: 1000 mA | ID8: 1000 mA | ID9: 1000 mA |

Either voltage < 1500 mV means no Apple signature (ID0). Cap ID6 at the board
maximum.

The divider appears on one of two mux banks, depending on Lightning
orientation. The other bank reads raw 0 (39 mV after the conversion offset).
Measure both banks and use the one with a valid signature:

| Bank | Route then D− select | Route then D+ select |
|---|---|---|
| 0 | `0x01`=`0x11`, then `0x1d` | `0x01`=`0x11`, then `0x1c` |
| 1 | `0x01`=`0x10`, then `0xd0` | `0x01`=`0x10`, then `0xc0` |

Each sample: 5 ms settle, ADC channel 1, restore `0x01` = 0, verify `0x03` = 0.

| Source (same Lightning to USB-A cable) | Orientation A (bank0 / bank1) | Flipped | Result |
|---|---|---|---|
| Apple A1357 (10 W) | 2652/1951 / raw 0 | raw 0 / 2647/1949 | ID4, 2100 mA |
| 20 W power bank, USB-A port | raw 0 / 2657/2657 | 2651/2652 / raw 0 | ID5, 2400 mA |
| Mac through a USB-A hub | raw 0 / raw 0 | not tested | no signature |

(values are D+/D− mV)

The route bytes `0x11`/`0x10` are what makes these readings work. They are
our derivation of what the vendor route handler would select. They are not a
replay of iPadOS's full routing sequence (see [Dead ends](#8-dead-ends-and-traps)).

### 2.4 Unidentified sources

Computers, hubs and generic USB chargers have no Apple divider and no Type-C
Rp on a USB-A cable. They stay at 425 mA. Missing evidence is not reported as
a 500 mA source. iPadOS has a separate BC1.2-style port-type path for these
sources (SDP/CDP/DCP, 500–1500 mA, [brickid.md](brickid.md#port-type-table)).
Linux does not implement it yet ([CH-04](experiments.md#ch-04)). A USB
enumeration by a host does not prove the host can supply more than 500 mA. PR #8
mapped any USB session to 2400 mA, and the review rejected that.

The prototype retries an unidentified result twice at 3 s, because a
half-inserted plug reads as no signature.

### 2.5 What iPadOS chose (reference)

From the IORegistry of a running iPadOS 17.7 on the same J71b (key
`IOAccessoryUSBCurrentLimit` on `AppleTriStarBuiltIn`):

| Connection | iPadOS limit | Notes |
|---|---|---|
| Mac dock, Lightning to USB-A | 1500 mA | Mac reported 500 mA allocation; a BC1.2 CDP may offer 1.5 A independently |
| Mac direct, Lightning to USB-C | 2400 mA | Mac sent a 2400 mA configuration override |
| Apple 140 W, Lightning to USB-C | 1500 mA | Same cable; CC reads in the 3 A band under Linux |
| 20 W power bank, Lightning to USB-C | 2400 mA | Same cable |
| Apple A1357, Lightning to USB-A | 2100 mA | Matches ID4 |

`IOAccessoryUSBChargingVoltage` was 5000 mV and `PortType` 12 in every case.
No `IOAccessoryIDBusPD` service was listed.

## 3. Withdrawal

### Signal

The PMIC raises AP GPIO38 (active low) for any pending event in its 30-byte
event block at `0x140`. Reading the block acknowledges all pending events
and releases the line. One GPIO serves every PMU client.

| Event | Meaning | Evidence |
|---|---|---|
| 176 | ADC conversion complete | Firmware registers it to the ADC completion callback; hardware: an ADC-only conversion produced exactly 176 |
| 56, 59 | PMIC GPIO8 / GPIO11 (event bank 48–71 = PMIC GPIO 0–23) | Firmware; present on a stable connection with or without USB traffic, no new edge |
| 72, 73 (with 76, 78, 80, 86) | Source detach and reattach | Hardware: every no-ADC source move; never 176 |
| 16, 40–43, 72, 77 | iPadOS charger callback subscription | Firmware |

Exact per-bit meanings among 72/73/76/78/80/86 are not established. The
safe rule does not need them:

- on every GPIO38 falling edge, first drop any grant to 425 mA, then read
  the block;
- 176 alone completes a pending ADC conversion and does not invalidate;
- 56/59 alone are benign;
- any other bit, a read error, or 176 with no conversion pending starts a new
  epoch, which requires a new identification;
- `0x18a` = 0 (no input) restores the 100 mA handoff.

### Timing

| Implementation | Edge to verified `0xa03` readback | Evidence |
|---|---|---|
| Bounded IRQ trials (read events, then write) | 6.7 ms, 58.5 ms, 6.8 ms | Hardware, three trials from 425 mA |
| Prototype `j71b_adaptive_charge` (write first in IRQ thread) | < 1 ms on every unplug | Hardware, commit `11073db` (module counters; raw log not kept in the archive) |

What these numbers do not cover:

- The time from the physical unplug to the PMIC asserting GPIO38 is not
  measured.
- The PASemi I²C driver can wait up to 100 ms per transfer, and the bus lock
  has no deadline. The worst case is therefore unbounded by design. The E1
  plan measures a histogram ([CH-03](experiments.md#ch-03)).

### Signals that do not work for withdrawal

| Signal | Why not | Evidence |
|---|---|---|
| `0x18a` polling | A quick A1357 to Mac move kept `0x42` on both sides of the snapshot | Hardware |
| USB gadget `configured`, `usb0` carrier | Stay set across unplug | Hardware |
| Tristar GPIO28 | No edge observed on source moves | Hardware |
| GPIO38 with a plain edge observer | Line stays low until someone reads `0x140` | Hardware |
| VBUS loss resetting `0xa03` | It does not | Hardware |

## 4. Battery gauge

BQ27540 on HDQ over UART5 (AP pin 173). Transport: UART-backed HDQ (`w1-uart`),
from HoolockLinux, generalized by Paul Praschl. Driver: `bq27xxx` with the
BQ27541 register profile under `ti,bq27540-hdq` (stack patch
`drivers/power/0001`). A missing HDQ response must return an error, not
`0xff`. Do not enable Paul's cached-telemetry mode on boards whose charger
guards need fresh `current_now`. A 30 s cache is too stale for that.

| Property | Unit | Note |
|---|---|---|
| `voltage_now`, `current_now` | µV, µA | Positive current = charging |
| `temp` | 0.1 °C | Pack temperature, not SoC temperature |
| `capacity`, `cycle_count` | %, count | |

First hardware read: 38 %, 3.805 V, −541 mA, 28.8 °C, 312 cycles. Net pack
current is input minus system load. At 425 mA, a bright screen can still
discharge ("Connected, discharging" must be a valid UI state).

## 5. Linux driver

The old stack's charging, now removed, worked like this:

- an out-of-tree module (`j71b_charge_policy`) was the single owner of
  `0xa03`;
- it accepted only `0x02` and `0x1c`;
- a root-only 15 s lease, renewed every 5 s by `j71b-charge-lease`, held
  `0x1c`;
- on expiry, stop or suspend it restored and verified `0x02`;
- it exposed `j71b-input/online` from `0x18a`.

The code is in the hardware repository's git history
(`runtime/j71b/j71b-charge-lease`, `runtime/j71b/j71b-check-platform`).

The stack now has one in-tree driver, `drivers/power/supply/apple_tristar_charger.c`
(patch `kernel/patches/drivers/power/0003`, binding
`apple,tristar-charger.yaml`, nodes in `t8010-ipad6.dtsi`). It is the
prototype's logic with its resources taken from the device tree:

| Prototype (out of tree) | Driver |
|---|---|
| Found the PMIC by I2C name, the Tristar by bus number | `charger@180` is a PMIC child; `apple,tristar` points at the `nxp,cbtl1610` node |
| Reconfigured AP GPIO 38 by MMIO, both edges | `interrupts-extended = <&pinctrl_ap 38 IRQ_TYPE_LEVEL_LOW>`; the oneshot thread runs until the line releases |
| Hard-coded register addresses | `reg` banks `status` (0x180), `control` (0xa00), `event` (0x140), `adc` (0x4000) |
| 2400 mA constant | `input-current-limit-microamp` |
| `module_param` state and counters | `power_supply` `lightning`: `online`, `usb_type`, `input_current_limit`, `current_max` (the source's advertisement); log lines carry the source and withdrawal time |
| PM notifier | `dev_pm_ops` suspend parks at `0x02` and forgets the source |
| Refused to start unless `0xa03` was `0x02`/`0x1c` and `0xa0c` was `0xf4` | Lowers any higher `0xa03` to `0x1c` at probe and continues; `0xa0c` is not checked. Lowering is always safe, and refusing left an unexplained high limit in place |

`usb_type` is `APPLE_BRICK_ID` for an Apple divider, `C` for a Type-C
source and `UNKNOWN` otherwise.

### Why one driver and not a source driver plus a charger

The planned design was the split Paul uses on the iPad 7: a port-controller
driver reporting the source's current, and the D2365 charger granting it.
The PMU interrupt, its read-to-clear event block and the ADC all belong to
the PMIC, and identification (ADC conversions completed by event 176) and
withdrawal (any other event) both need them. A split would need an event
`irq_chip` and an IIO ADC first, and withdrawal would go through
`power_supply_changed()`, which defers to a workqueue. Keeping one driver
keeps the measured sequence: write the fallback in the interrupt thread,
then attribute events. If more PMIC users of the event block appear, an
`irq_chip` for it is the next step, and the source identification can move
into its own driver then.

Points carried over from the prototype:

- Write the conservative limit first, then attribute events. A grant must
  never wait for HDQ battery reads or a policy mutex held across them.
- Round grants down: `code = (mA − 75) × 2 / 25`.
- Suspend and shutdown: drop to handoff and forget the source; sleep can
  hide a swap.

Paul's current `apple_d2365_charger` (J172 match data): when the Bellatrix
supplier reports online, it writes `0x6c` (1425 mA) and polls every 5 s.
It does not identify the source. The generic `apple,d2365-charger`
compatible has no policy.

## 6. Method: how the facts were found

1. **Read the ADT.** It gives the chips, their I²C addresses, interrupts and
   the board policy (`usb-input-limit-max`, calibration, `function-*`
   bindings such as `function-brick_id_voltage` → ADC channel 1, gain 65536).
   Do not publish raw ADT or the calibration bytes.
2. **Read the iPadOS kernelcache.** The A10 kernelcache is a legacy
   `MH_EXECUTE` with no symbols and no `LC_FUNCTION_STARTS`, so
   `ipsw kernel extract` does not work. A small script found the embedded kext
   headers and rebuilt `ADRP+ADD` string references, which named the code.
   Follow vtables of the **concrete** class. Details:
   [ios-firmware.md](ios-firmware.md).
3. **Observe iPadOS at runtime.** Run a read-only IORegistry dump on a
   jailbroken iPadOS for each source. It shows what Apple's policy chose,
   which is the reference answer for Linux.
4. **Bounded on-device probes from a RAM boot.** One module per question
   (ADC only, events only, one mux pair). Each has a one-shot marker, a fixed
   read budget for `0x140`, full restoration and an independent postcheck.
   The input limit stays at or below 425 mA. Wi-Fi SSH is the transport,
   because mux changes stalled USB networking more than once.
5. **Manual known-source current trials.** Only at the end, only on a
   source known to supply the current, with a short lease and readback.
6. **Prototype owner** combining identification, grant and withdrawal.

Register dumps were used sparingly. A read is not side-effect-free here:
`0x140` is read-to-clear, `0x4000` starts a conversion, and Tristar status
reads may interact with discovery.

## 7. Order of work on a new board

Each step needs the one before it. Steps 1–4 never raise the limit.

1. **Baseline** ([CH-00](experiments.md#ch-00)). Record what iBoot leaves:
   input limit, pack cap, status byte, gauge current, for each source type.
   That is the device's safe default.
2. **Wiring from the ADT.** Find the port controller, PMIC, charger node,
   PMU interrupt pin and ADC channel for the detect voltages
   ([CH-02](experiments.md#ch-02), [CH-08](experiments.md#ch-08)).
3. **Gauge.** It must work before any charging work, because the guards
   read it.
4. **Event attribution.** Own the PMU interrupt. Do one ADC-only conversion
   and one unplug/replug with no ADC, and record which events appear.
   Expect 176 and 72/73 if the board matches J71b.
5. **Withdrawal at the default.** At ≤ 425 mA, measure edge to verified
   readback over many unplugs and fast swaps, with a poller that counts any
   detach the IRQ path missed ([CH-03](experiments.md#ch-03)). Never grant
   more than the default before this is measured.
6. **Identification, detector only.** Capability frame, then CC or both
   divider banks. Log what you would grant; write nothing.
7. **Grants**, one source type at a time, below the source's label, with the
   gauge logged and a meter if available ([CH-14](experiments.md#ch-14)).
8. Unknown sources stay at the default throughout.

## 8. Dead ends and traps

| Trap | What happened | Do instead |
|---|---|---|
| Forcing monitor modes from `0x01` = 0 | The vendor monitor rejects `0x01` unless `(v & 7) == 1` or `(v & 0x70) == 0x10`; forced modes 1/2/4/5 gave readings that failed the BrickID floor and could not tell a Mac from an A1357 | Select a guard-passing route (`0x11`/`0x10`) first |
| `0x81` as "J71b init" | Belongs to a neighbouring dispatch table | Ignore |
| ADC channel 1 alone | Raw 0 (39 mV) on every source | It is a mux output, not a rating register |
| Type-C via route 11 + modes 4/5 | Wrong path; one try dropped the source about 20 ms after `0x03` excitation | Type-C uses the direct monitor with `0xc0`/`0x0c` |
| THS7383 vtable taken as CBTL1610 | Wrong response layout (7 bytes at `0x39`) | CBTL1610 override: 8 bytes at `0x20` + CRC |
| D2365 disassembly addresses | Old listing shifted by +0x80 | Use the corrected listing |
| Every GPIO38 edge = source change | ADC completion (176) asserts the line; classification never finished | Demultiplex 176 |
| Masking or ignoring 72 during sampling | Would hide a real swap | Never mask; withdraw and retry |
| Full battery telemetry inside every guard | One Type-C acquisition took 166 ms | Full check at phase boundaries, fast guards between transfers (23 ms) |
| Treating iPadOS 1500 mA vs Mac 500 mA as overdraw | A CDP can offer 1.5 A beside a 500 mA allocation | Measure BC1.2 before concluding |
| Mislabelled sample (1736/1077 mV called "Mac") | It was the wall charger | Record the source with every sample |
| BrickID boundary 2200 mV (PR #8) | J71b firmware boundary is 2184 mV | Use 1500/2184/2880 from J71b firmware |
| USB session = 2400 mA (PR #8) | Grants board maximum to any host | Host allowance needs BC1.2/PD evidence |
| Polling CC every 5 s (PR #8) | Misses the 60 ms tSinkAdj | Open, see CH-07 |
| Mux sampling during USB data | USB networking stalled; a gadget rebind restored it | Sample with Wi-Fi as transport; skip sampling while a USB link is active |
| Writing `0xa00` around limit changes | iPadOS toggles bit 1; unnecessary for Linux | Write `0xa03` only |
| Copying another board's helpers (CBTL1614 IDBus, Hydra DMON) | Not the J71b path | Trace this board's concrete class |
| Draining `0x140` from several modules | Each read acknowledges every client's events | One owner of the block |

## 9. Other approaches

| Work | Board | Approach | Notes |
|---|---|---|---|
| Paul Praschl (Pauli1Go): `apple_bellatrix_power` + `apple_d2365_charger` | iPad 7 J171/J172 | Bellatrix reports presence only (`0x16` bit 1 VBUS, `0x17` provider/consumer bits, chip ID `0x73` = `0x74`); charger writes 1425 mA when online | Source current on Bellatrix unknown ([CH-01](experiments.md#ch-01)) |
| Paul Praschl: `apple_sn2400_charger` | iPhone 7 Plus D111 (D11 untested) | SN2400 at i2c1 `0x75` measures VBUS and IBUS; ramps the input limit from 300 mA in 25 mA steps and folds back on > 400 mV droop or VBUS < 4.0 V; DT `input-current-limit-microamp` = 2 A | Works without identifying the source, because SN2400 has a VBUS ADC |
| Martin Puškáč (ipad-nixos), delivered by micro23: `j81_d2207_charger` | iPad Air 2 J81 (A8X) | D2207 input limit `0x04c0`, VBUS current/voltage via D2207 ADC, completion polled in event register `0x005b` | Read-to-clear polling; see `notes/2026-10-28/ipad-air2-analysis.md` |
| Berkay Kösebay (brkosebay): linux-a9 charge7–10, PR #6 (J71t) and PR #8 (J71b port) | iPad 5 J71t; port to J71b | BC1.2 primary/secondary, GetID over the Tristar, CC, per-unit calibration ladder 500/1000/2100/2400 mA | Reviewed: USB session → 2.4 A and 5 s CC refresh are defects; one J71b capture blocked before detection; J71b-unverified |

Claims from PR #8 that need checking are marked as such in the reference
files. They are the BC1.2 thresholds, the GetID registers `0x5d..0x61`
(request `0x74`), the USB-C cable ID `10 09 08 00 00 00`, an ADC-ready bit in
PMIC `0x156`, and a 26 ms attach reset of `0xa03` on J71t.

## 10. Records

The raw research is in the OrchardLinux workspace. It is not published,
because receipts contain device identifiers. Paths are relative to the
workspace root:

| Topic | Location |
|---|---|
| Consolidated handoff (2026-10-22, with later notes) | `charging/IPAD-CHARGING-HANDOFF.md` |
| Static trace, iOS observation, early probes | `ipadlinux/experiments/power-001/` (`CHARGER-SOURCE-TRACE.md`, `IOS-SOURCE-OBSERVATION.md`, `IRQ-INVALIDATION.md`, `CLASSIFICATION-V1.md`, `ROUTE11-TRIAL.md`, `CHARGER-DRIVER.md`) |
| Hardware logs of the early phase | `ipadlinux/logs/power-001-*.log` |
| Event attribution, two-bank detector, Type-C, capability frame | `charging/event-attribution/` (`DETECTION-STATUS.md`, `README.md` and one `*-RESULT.md` per trial) |
| Status and E1 plan | `worktrees/charging-deluxe/docs/charging/` |
| Prototype owner | hardware commit `11073db` (`kernel/modules/j71b/power/j71b_adaptive_charge.c`, `j71b-capability.h`, `j71b-event-demux.h`) |
| PR #8 port and review | `worktrees/charging-experiments/hardware/kernel/modules/j71b/power/`, `examples/charging-pr8/` |
| J71t PR review (charging sections) | `notes/2026-10-28/j71t-pr-review.md` |
| Paul's drivers | stack `kernel/.work/apple/source/drivers/power/supply/` |

## Credits

Paul Praschl (Pauli1Go) wrote the D2365 charger, Bellatrix, SN2400 and HDQ
work and the J172/D111 device trees. The HDQ transport comes from HoolockLinux.
Berkay Kösebay (brkosebay) did the iPad 5 charging work in linux-a9 and
PR #6/#8. The iPad Air 2 D2207 charger is Martin Puškáč's (ipad-nixos), delivered
to Orchard by micro23. Public Lightning research
by nyansatan (https://nyansatan.github.io/lightning/) was used as background.

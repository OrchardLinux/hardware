# J71b measurement summary

All measurements were taken on one iPad 6 Wi-Fi (J71b) in Linux RAM boots,
2026-10-20 to 2026-10-28. Pack current is the BQ27540 reading: positive =
charging, net of system load. Screen brightness and load were not
controlled unless stated. No external meter was used, so actual VBUS voltage
and input current are unmeasured. Raw receipts are in the research archive
([README.md §10](README.md#10-records)).

## Input-limit trials

All but the first row were manual, known-source trials, each with a short
lease and verified restoration to `0x02`.

| Code (nominal) | Source | Pack current before → after | Notes |
|---|---|---|---|
| `0x12` (300 mA) | Mac | −0.55 A → −0.29 A | Discharge reduced |
| `0x1c` (425 mA) | Mac, screen off | −282 mA → +127/+131 mA | Lease and expiry tested; 52 s charging interval |
| `0x32` (700 mA) | Known wall charger | about neutral to slightly positive, bright screen | Restore came at 16.4 s against a 15 s nominal lease |
| `0x4a` (1000 mA) | Apple 140 W, USB-C cable | +0.23 to +0.39 A | Brightness 1563 |
| `0x72` (1500 mA) | Apple 140 W | +0.73 to +0.88 A | |
| `0x9a` (2000 mA) | Apple 140 W | +1.16 to +1.32 A | |
| `0xba` (2400 mA) | Apple 140 W | +1.40 to +1.55 A | |
| `0xba`, 172 s hold | Apple 140 W | pack 31.5 → 32.4 °C, 21 → 22 % | Then restored `0x02`; pack current negative again with screen on |

Records: `ipadlinux/logs/power-001-charge-step-live-test.log`,
`-charge-425-screenoff-run.log`, `-manual700-device-result.log`,
`-apple140-{1000,1500,2000,2400}-result.log`,
`-apple140-2400-lease-final.log`.

Lessons:

- Software timers are not hard real time (16.4 s for a 15 s lease).
- A 2400 mA setting that "works" shows only that the pack charged faster.
  It does not show the source contract or the actual draw.

## Source signatures

USB-A Apple divider: see [brickid.md](brickid.md#measured-signatures-j71b-same-lightning-to-usb-a-cable).
Raw ADC values from the two-bank detector:

- A1357: D+ raw 2134 → 2644 mV, D− raw 1560 → 1944 mV;
- inactive bank: raw 0 → 39 mV.

USB-C CC (one orientation-selected sample each, `0x01` = `0xc0`):

| Source | Raw | mV | Record |
|---|---|---|---|
| Apple 140 W | 1394 | 1741 | `charging/event-attribution/TYPEC-WALL-RESULT.md` |
| Mac USB-C | 755 | 961 | `TYPEC-MAC-RESULT.md` |
| 20 W power bank | 1426 | 1780 | `TYPEC-POWERBANK-RESULT.md` |
| Power bank, plug flipped (status still orientation 1) | 1406 | 1756 | `TYPEC-POWERBANK-FLIP-RESULT.md` |
| Power bank, joint with capability frame | 1414 / 1418 / — | 1766 / 1770 / 1792 | `TYPEC-JOINT-RESULT.md`, `TYPEC-LIVE-TIMING.md`, `TYPEC-FRESH-RESULT.md` |

## Tristar status `0x0c` sequence (power bank, USB-C cable)

| Phase | `0x0c` | Bit 7 | Bit 6 | Bit 2 |
|---|---|---|---|---|
| Attached | `0xa3` | 1 | 0 | 0 |
| Unplugged | `0x24` | 0 | 0 | 1 |
| Replugged, rotated | `0xa3` | 1 | 0 | 0 |
| Rotated again | `0xa3` | 1 | 0 | 0 |

Record: `TYPEC-STATUS-SEQUENCE-RESULT.md`.

## PMU event captures

| Situation | Bits in snapshot | GPIO38 | Record |
|---|---|---|---|
| First read after boot (Mac) | 40, 56, 58, 59, 72, 73, 76, 78, 80, 81, 86 | low → released | `RESULT.md` |
| Stable Mac, 5 s idle | 56, 59 | no new edge | `RESULT.md` |
| Stable Mac, quiet vs 8 MiB USB traffic | 56, 59 both | no new edge | `USB-CONTROL-RESULT.md` |
| ADC-only conversion, no mux | 56, 59, **176** | new falling edge | `ADC-RETRY1-RESULT.md` |
| Mac → Apple 10 W, detach | 56, 59, 72, 73, 76, 86 | edge; `0x18a` = `0x00` | `SWAP-RESULT.md` |
| Wall → Mac, detach | 56, 59, 72, 73, 76, 86 | edge | `SWAP-OBSERVE-RESULT.md` |
| Wall → Mac, reattach | 56, 59, 72, 73, 78, 80, 86 | edge; `0x18a` = `0x40`, then `0x42` 116 ms later | `SWAP-OBSERVE-RESULT.md` |
| Combined: Mac ADC, then move to wall | ADC: 56, 59, 176 (epoch unchanged); detach and attach advanced epoch 0 → 1 → 2 | | `DEMUX-RESULT.md` |
| A1357 ↔ Mac (early) | 9–10 pending bits; 72 the only charger-callback bit; `0x18a` `0x42` before and after | edge | `ipadlinux/experiments/power-001/IRQ-INVALIDATION.md` |

## Withdrawal

| Trial | Start limit | Edge → verified safe limit |
|---|---|---|
| One-shot Mac → A1357 | 425 mA | 6686 µs (line went low again later; durability failed) |
| Bounded A1357 → Mac | 425 mA | 58453 µs (two assertions 9.84 s apart, both serviced) |
| Quick Mac → A1357 | 425 mA | 6822 µs (two assertions 2.289 s apart) |
| Prototype, every unplug | granted (up to 2400 mA) | < 1 ms |

## Prototype session (hardware commit `11073db`, 2026-10-28)

The commit record lists the sources tested:

- power bank USB-A → ID5 2400 mA;
- Mac hub → no signature, 425 mA;
- Apple 140 W USB-C in both orientations → 3 A class, granted 2400 mA;
- a Mac USB-C port → 3 A.

Withdrawal was under 1 ms on every unplug.

The session's kernel log is not in the research archive. The module
exported `withdrawals`, `last_withdraw_us` and `max_withdraw_us`. Repeat
the session with logging before relying on these numbers in a review
([CH-03](experiments.md#ch-03)).

## Gauge

First read: 38 %, 3.805 V, −541 mA, 28.8 °C, 312 cycles. Later readings
tracked limit changes and screen load. Pack temperatures recorded in the
trials ranged from about 26 to 38 °C.

## iOS observation

See [README.md §2.5](README.md#25-what-ipados-chose-reference). Records:
`ipadlinux/logs/power-001-ipados-registry-{mac-usba,mac-usbc,wall140,bank20,apple10w,apple10w-later-detached,apple10w-reconnect}.log`.

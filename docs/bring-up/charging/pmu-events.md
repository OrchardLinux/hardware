# PMU events and withdrawal

## Mechanism

- The D2365 keeps 240 event bits in a 30-byte block at `0x140`. Event *n* is
  byte *n*/8, bit *n*%8.
- Masks are in the matching block at `0x1c0`. A plain I²C byte write there
  reads back unchanged (J71b, 2026-10-29), so Linux cannot mask events yet
  and runs with the mask iBoot leaves ([CH-15](experiments.md#ch-15)).
- Any unmasked pending event drives AP GPIO38 low. On J71b the ADT gives the
  PMU interrupt as GPIO38 with flags 1.
- Reading the 30-byte block acknowledges everything pending and releases
  the line (hardware). The release shows as a rising edge on a both-edge
  counter; do not count it as a source event.
- iPadOS's physical handler reads all 30 bytes and dispatches set bits to
  subscribers (firmware).

Consequences:

- **One owner.** Two readers each acknowledge the other's events. The final
  stack needs a D2365 `irq_chip` that owns the block and hands events to
  consumers.
- A plain GPIO edge observer sees nothing useful. The line stays low until
  the block is read.
- Pending bits can refill without a new edge: 56/59 reappeared on a stable
  connection with no new falling edge. A nonzero snapshot is therefore not
  the same thing as a new interrupt.

GPIO38 on J71b (pinctrl `apple,t8010-pinctrl` at `0x20f100000`, pin offset
`38 × 4`): the original configuration was mode 0, input enabled, interrupt
group 7. The prototype requests it as both edges and acts on the falling
edge (line low).

## Event table

| Event | Meaning | Evidence |
|---|---|---|
| 16, 40, 41, 42, 43, 72, 77 | Subscribed by iPadOS charger callback | Firmware |
| 16, 40–43, 72, 73 | Clear in iPadOS's initial mask (polarity and full board setup not derived from this) | Firmware |
| 48–71 | PMIC GPIO 0–23 (event − 48 = GPIO index) | Firmware (D2365 vtable `0xfffffff006fc18f0` +0x590 → `…6b7f3a0`); ADT pairs Hall GPIO0/10 with events 48/58 |
| 56, 59 | PMIC GPIO8, GPIO11. Present on a stable connection, quiet or with 8 MiB USB traffic, without a new GPIO38 edge | Firmware, hardware |
| 73 | Also latches every few seconds while the input current approaches 2 A, with the same source attached. Not a source event: iPadOS's charger does not subscribe to it, and Linux leaves it out | Hardware (J71b RAM test, 2026-10-29), firmware |
| 72, 73 (+76, 86) | Source detach | Hardware: every no-ADC unplug; first seen as the only charger-callback bit on A1357 ↔ Mac moves |
| 72, 73 (+78, 80, 86) | Source reattach | Hardware |
| 176 | ADC conversion complete | Firmware (registration `0x1601`, callback `…6b7c3b4`); hardware (one ADC-only conversion: new 176, nothing else new) |

Initial snapshots after boot contained leftover bits
40, 56, 58, 59, 72, 73, 76, 78, 80, 81, 86. Acknowledge once at start and
ignore that first snapshot.

Mask bytes read once (not interpreted): `0x1c2` = `0x00`, `0x1c5` = `0xf0`,
`0x1c9` = `0x5a`.

## Dispatch rule (prototype, hardware-tested)

```
on GPIO38 falling edge (hard IRQ): timestamp; flag revoke
IRQ thread:
  if granted above 425 mA and no ADC pending: write 0xa03 = 0x1c, read back
  read 0x140 (up to 3 times until the line is high; still low -> fault, hold 425 mA)
  for each set bit:
    56, 59        -> ignore
    176           -> complete the pending ADC; if none pending, invalidate
    anything else -> invalidate
  invalidate -> epoch++ (before waking any ADC waiter) -> re-identify
poller (100 ms): 0x18a == 0 -> write 0x02 (handoff); 0x40 -> no grant
```

Two points matter:

- Mixed events (176 together with 72) must invalidate **before** the ADC
  result is accepted. Otherwise a conversion that straddles a swap is
  accepted.
- Do not mask 72 during sampling and do not ignore edges in the sampling
  window. Both extremes fail. Ignoring edges hides real swaps. Treating
  every edge as a swap, as the first classifier did, aborts on the
  classifier's own ADC completion.

Gap in the prototype: it skips the immediate write when an ADC is pending
(`!events.waiting`). A swap during a conversion then relies on the event
attribution that follows. Close this in the final driver.

## Timings

| Measurement | Value | Record |
|---|---|---|
| Unplug → `0xa03` still `0x1c`, until software restored | 4.9 s (lease period) | `ipadlinux/logs/power-001-pmic-source-transition-a1357.log` |
| Unplug → `0x18a` = 0 → reattach `0x40` → `0x42` | `0x40` lasted 116 ms and 119 ms in two captures | `charging/event-attribution/SWAP-OBSERVE-RESULT.md`, `DEMUX-RESULT.md` |
| ADC select log → GPIO38 low | 8.4 ms (includes logging and I²C) | `ADC-RETRY1-RESULT.md` |
| IRQ fall → verified `0x02`, bounded trial (events read first) | 6686 µs, 58453 µs, 6822 µs | `ipadlinux/experiments/power-001/IRQ-INVALIDATION.md` |
| IRQ fall → verified `0x1c`, prototype (write first) | < 1 ms, every unplug | out-of-tree prototype (its commit record; the module exported `last_withdraw_us`, `max_withdraw_us`) |
| Type-C acquisition, trigger → qualified | 166 ms (full telemetry in guards), 23.1 ms (fast guards) | `TYPEC-LIVE-TIMING.md`, `TYPEC-FRESH-RESULT.md` |
| Type-C repeated acquisition | second renewal took 45.4 ms, over a 30 ms budget | `TYPEC-FRESH-RESULT.md` |

Unmeasured:

- the time from physical unplug to GPIO38 assertion;
- the I²C worst case. `i2c-pasemi` waits up to 100 ms per transfer, and
  the bus lock is a blocking RT mutex with no deadline;
- the end-to-end path in `apple_tristar_charger` (CH-03).

## Earlier negative results (keep for orientation)

- A gpio-keys observer on GPIO28 (Tristar IRQ) and GPIO38 saw no edges on
  source moves. GPIO38 was never released because nothing read `0x140`.
- A two-sample classifier aborted on a stable Mac connection. The edge in
  its sampling window was most likely its own ADC completion. Event 176 was
  identified later and explains it.
- Masks, polarity and full iPadOS setup were not replicated. Linux worked
  with the masks as iBoot left them.

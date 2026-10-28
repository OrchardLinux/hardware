# Charging during bring-up

What Orchard learned bringing up charging on J71b (iPad 6), and how to
approach it on a new device. On the new stack, charging belongs in kernel
`power_supply` drivers. A userspace lease or a bring-up policy module is
not part of the final stack.

## Why a bring-up needs care

The PMIC's input-current limit decides how much the board draws from the
source. Raising it above what the attached source can deliver can overload
that source. iBoot leaves a conservative setting. Linux must only raise it
for a source it has identified, and must drop it again as soon as that
source may have changed.

## What the old J71b stack did (removed)

- `j71b_charge_policy` (out-of-tree module) plus a userspace lease
  (`j71b-charge-lease`): a single owner held the input limit at ≤425 mA
  (`0xa03=0x1c`), renewed every 15 s, and restored the handoff value `0x02`
  when it stopped.
- A distro service checked the platform and started the lease at boot.

Both were removed in the move to the torvalds-based stack. Their code is in
the hardware repository's git history (`runtime/j71b/j71b-charge-lease`,
`runtime/j71b/j71b-check-platform`).

## Measured on J71b

- The BQ27540 gauge reports over HDQ through the standard `power_supply`
  interface (voltage, current, capacity, temperature, cycles).
- Source identification:
  - USB-A Apple chargers by their D+/D- divider (BrickID), on both mux banks;
  - USB-C by the CC level on the orientation-selected lane (660/1230 mV
    bands);
  - the CBTL1610 capability frame distinguishes USB-A from USB-C.
- Prototype adaptive owner (`j71b_adaptive_charge`, hardware commit
  `11073db`, branch `feature/charging-deluxe`). It granted the identified
  current up to the 2400 mA board maximum: power bank USB-A (ID5), Apple
  140 W USB-C in both orientations and a Mac USB-C port (3 A). It kept
  unidentified sources (Mac hub) at 425 mA.
- Withdrawal: every PMU (GPIO38) falling edge dropped the grant to 425 mA
  from the IRQ thread. Measured under 1 ms on every unplug.
- VBUS loss does not reset the input limit. Only software withdrawal does.
- PMU event attribution: 176 is ADC completion, 56/59 are USB endpoint
  events (both benign). Source moves produce 72/73.

Open at the time of writing: host (BC1.2/CDP) allowance, USB PD, suspend,
and live USB-C Rp changes (CC is sampled on demand).

## Checking a new device

1. Before any charging code runs, record what iBoot leaves: the input
   limit register and the battery current from the gauge, with each source
   type attached. That is the device's safe default.
2. Identify the source-detection hardware (BrickID divider, CC sampling,
   capability frame) from the ADT and the PMIC documentation you have.
3. Measure withdrawal first: how fast an unplug or source change reaches the
   kernel, and how long a write to the input limit takes. Never grant more
   than the default before this is measured.
4. Only then grant identified currents, one source type at a time, with
   the gauge current logged.

# Charging experiments

Each entry is one precise experiment that closes a gap in
[the charging guide](README.md). Pick one that matches your device from the
[per-device menu](#per-device-menu), run it, and send back what the entry
asks for. Entries say which facts come from our sources and mark our own
reasoning as *inferred*.

## Rules for every experiment

1. **Never set the input limit above the device's default before withdrawal
   is measured on that device** ([CH-03](#ch-03)).
   - J71b: fallback 500 mA (`0xa03` = `0x22`; 425 mA `0x1c` in the old stack), handoff 100 mA (`0x02`).
   - Other boards: the default is what [CH-00](#ch-00) records.
   - No entry below raises the limit unless it says so.
2. **One owner.** Do not run two drivers or modules that write the input
   limit or read the PMU event block. On D2365 boards, `0x140` is
   read-to-clear and shared, so each read acknowledges every other client's
   events.
3. **No scans.**
   - Read only the registers an entry names. Do not dump unknown ranges or
     scan I²C buses.
   - Do not `cat` regmap debugfs `registers` files for the PMIC. *Inferred*:
     that reads the event block and may start side effects.
4. **Restore the mux.** After any Tristar mux sample, `0x01` must read back
   its original value and `0x03` must read 0. If not, stop and reboot.
5. **Battery window.** Pack 5–40 °C and 3.2–4.35 V, state of charge between
   20 % and 90 % for current measurements. Stop if the pack passes 40 °C.
6. **RAM boot.** Internal storage read-only. Use Wi-Fi SSH as the transport
   when you touch the Tristar mux; mux changes have stalled USB networking
   on J71b.
7. **Privacy.** Never send serial numbers, ECID, MAC addresses, Wi-Fi
   credentials, SysCfg, `usb-input-limit-calibration` bytes, raw ADT dumps
   or raw Lightning identification frames. Send decoded values.
8. **Label everything.** For each sample, record the charger model, the
   cable, which end you moved, and the plug orientation. Put a dot of tape on
   one side of the Lightning plug so "A" and "B" are unambiguous.

Tooling: Orchard does not ship a register probe yet. The J71b prototype
(hardware branch `feature/charging-deluxe`, commit `11073db`,
`kernel/modules/j71b/power/j71b_adaptive_charge.c`) shows how to:

- own GPIO38;
- read the event block once per edge;
- sample the mux and restore it;
- time a withdrawal.

It refuses to load on anything but `apple,j71b`. Port the parts you need
and keep the guards.

What to send back (all entries): the device, board, iOS version if used,
kernel commit, the table the entry asks for, and anything that surprised
you. Post it to the Orchard hardware repository as an issue or pull request
against this file.

---

<a id="ch-00"></a>
## CH-00: Baseline (what iBoot leaves)

- **Devices**: every device.
- **Why**: this is the safe default each later step falls back to. It is
  also the first sign of whether a board matches J71b.
- **Equipment**: the device, Linux RAM boot with the gauge working, a Mac or
  PC, one USB charger.
- **Time**: 30 min. **Risk**: none (reads only).

Steps:

1. With the stack's drivers loaded and no charging owner writing anything,
   read once per state. The states are: no cable, cable to a computer, cable
   to a wall charger.
   - D2365 boards (J71b, J72b, J71s/t, J72s/t, J171/J172):
     - `0xa03` (input limit);
     - `0xa0c` (pack cap);
     - `0x18a` (status);
     - Paul's charger driver reports `0xa03` decoded as `input_current_limit`
       of the `charger` supply, if the board has that node.
   - SN2400 boards (D11/D111): the SN2400 charger supply's
     `input_current_limit`, `voltage_now` and `current_now`.
   - Gauge: `capacity`, `voltage_now`, `current_now`, `temp` of
     `/sys/class/power_supply/battery`.
2. Repeat the "cable to computer" row after a fresh boot with the cable
   already attached.

Send back: a table of state × register values and gauge values.

J71b reference: `0xa03` = `0x02`, `0xa0c` = `0xf4`, `0x18a` = `0x42` with a
source and `0x00` without.

---

<a id="ch-01"></a>
## CH-01: Bellatrix source current (iPad 7)

- **Devices**: J171, J172.
- **Why**: Paul's Bellatrix driver reports only whether power is present
  (`0x16` bit 1 VBUS, `0x17` bit 0 provider, bit 1 consumer, chip ID `0x73` =
  `0x74`). The J172 charger then writes a fixed 1425 mA (`0x6c`) for every
  source. To grant per source, we need to know where the iPad 7 learns the
  source's current.
  - *Inferred*: Bellatrix replaces Tristar on these boards.
  - *Inferred*: 1425 mA is above what a 500 mA computer port allows. Avoid
    long sessions on plain computer ports until this is understood.
- **Equipment**: iPad 7 with a jailbroken iOS (for part A), Linux RAM boot
  (part C). Sources:
  - Mac or PC USB-A port;
  - Mac USB-C port;
  - Apple 5 W, 10 W or 12 W USB-A brick (whichever you have);
  - a USB-C charger of 18 W or more with an Apple USB-C to Lightning cable;
  - a generic USB-A charger.
- **Time**: A 1 h, B 1–3 days, C 1 h. **Risk**: A and B none; C read-only.

Steps:

A. iOS reference (procedure in [CH-13](#ch-13)). For each source, record:
   - `IOAccessoryUSBCurrentLimit`, `IOAccessoryUSBCurrentLimitBase`,
     `IOAccessoryUSBChargingVoltage`, `PortType`, `AdapterInfo`;
   - which accessory/port classes exist. Is there an `AppleTriStarBuiltIn`
     equivalent? What is the Bellatrix class called?

B. Offline, from the iPad 7 kernelcache (method in
   [ios-firmware.md](ios-firmware.md)):
   1. Find the Bellatrix kext (search the string `Bellatrix`).
   2. Follow its concrete vtable to the code that produces the value
      published as `IOAccessoryUSBCurrentLimit`.
   3. Record which Bellatrix registers and bits it reads, and how it
      decodes them (BrickID-like voltages, Type-C Rp, BC1.2 type, PD).

C. Linux, only for registers found in B, or else only `0x16` and `0x17`:
   1. Add a one-line debug print of the raw values in
      `apple_bellatrix_update()`.
   2. Record them per source and per plug orientation.
   3. No Bellatrix writes.

Send back:

- A: the iOS table per source;
- B: the register/bit/decode table with kernelcache build and addresses;
- C: the raw values per source.

---

<a id="ch-02"></a>
## CH-02: iPad 5 wiring check

- **Devices**: J71t, J72t (A9, S8003); J71s, J72s (A9, S8000). J72b (iPad 6
  cellular) for the ADT part only, as a diff against J71b.
- **Why**: Berkay's J71t work drives a CBTL1610 at i2c0 `0x1a` through a
  D2365, like J71b. If the PMU interrupt pin, ADC channel and event numbers
  match, the J71b drivers apply with board data only.
- **Equipment**:
  - Part A: an IPSW for the device, or an ADT dump from m1n1.
  - Part B: Linux RAM boot with the gauge working, a Mac, an Apple USB-A
    brick, Wi-Fi SSH.
- **Time**: A 1 h, B 2 h. **Risk**: A none. B is low: an ADC conversion and
  event-block reads, with no limit writes.

Steps:

A. ADT, from the device tree only. Send the values; do not attach the ADT.
   1. `tristar` node: `compatible`, I²C bus and `reg`, `interrupt-parent`
      and `interrupts`, every `function-*` property name, and for
      `function-brick_id_voltage` its channel and gain. J71b: `adcV`,
      channel 1, gain 65536.
   2. `charger` node: `compatible`, `usb-input-limit-max`,
      `min-charge-capable-current`, and whether `usb-input-limit-calibration`
      exists. Do not send its bytes.
   3. PMU/PMIC node: `compatible`, I²C `reg`, `interrupts` and which AP GPIO
      that is (J71b: GPIO38).
   4. Gauge node: `compatible`, transport.

B. Live, at the default limit.
   1. Do [CH-00](#ch-00) first.
   2. Make sure no other driver handles the PMU interrupt. PR #6 adds a
      D2365 `irq_chip`; do not load it at the same time.
   3. Request the PMU GPIO from A3 as an edge interrupt.
   4. Read the 30-byte block at `0x140` once to clear old events and
      confirm the line goes high.
   5. Idle 5 s, then read the block once. Expected on J71b: only 56/59, no
      new edge.
   6. Start one ADC conversion with no Tristar mux change: write `1` to
      `0x4000`. On the edge, read the block once, then read the 2-byte
      result at `0x4001`. Expected: new 176, result about 39 mV.
   7. Ask a second person to unplug the cable, wait 5 s and plug it back.
      On each edge read the block once. Expected: 72/73 on both;
      `0x18a` = `0x00` while unplugged, then briefly `0x40` before `0x42`.
   8. Stop if the line stays low after a read. Never read the block in a
      loop.

Send back:

- A: values per node;
- B: the bit lists per step, `0x18a` values, the ADC raw value and
  whether the line released.

---

<a id="ch-03"></a>
## CH-03: Withdrawal latency, end to end (E1)

> **Result on J71b (2026-10-28):** 980–1006 µs from the PMU interrupt to a
> verified write, four unplugs, with `apple_tristar_charger`
> ([measurements](measurements.md#driver-session-apple_tristar_charger-2026-10-28)).
> Still open: physical unplug to GPIO38 assertion, and other boards.

- **Devices**: J71b first; any D2365 board after CH-02.
- **Why**: higher current is only safe if a detach or swap drops the limit
  before the new source is overloaded.
  - The prototype measured < 1 ms from the GPIO38 edge to a verified write.
    That session's log is not archived.
  - The I²C driver can wait up to 100 ms per transfer.
  - The time from physical unplug to the edge is unknown.
  - Plan: `worktrees/charging-deluxe/docs/charging/E1-PLAN.md` (local).
- **Equipment**: J71b, Apple A1357 (or another ID4/ID5 brick), the 20 W bank
  or another USB-A ID5 source, a Mac with a USB-A hub, Wi-Fi SSH.
- **Time**: about 20 min of user-paced swaps in one RAM boot. **Risk**: low.
  The limit never exceeds 425 mA. Grants are logged as `would_grant_ma`,
  not written.

Steps (one module that replaces the default owner for the session):

1. **Admission**: `0x18a` presence plus a GPIO38 edge carrying 72/73. Skip
   sampling if the USB gadget link is active (result: unknown).
2. **Detect**: the two-bank USB-A method ([brickid.md](brickid.md)).
   Restore and verify the mux after each bank.
3. **Events during sampling**: 176 alone completes the conversion. 72/73, a
   status change, or an unknown bit aborts the run and starts a new epoch.
4. **Log the grant only**: ID4 → 2100, ID5 → 2400. The real limit stays
   `0x1c`.
5. **Withdraw for real**:
   - hard IRQ timestamps the edge;
   - the thread writes `0xa03` = `0x02` and reads it back;
   - log the I²C start, end and readback times.
6. **Miss detector**: an independent 10 ms poller of `0x18a`. A zero seen
   before the IRQ path withdrew counts as a miss.
7. **Histogram** of every PMIC transfer time, with the screen on and Wi-Fi
   busy.

Matrix:

| Row | Action | Expect |
|---|---|---|
| 1 | A1357 attach, orientation A | ID4, would grant 2100, no epoch change from ADC |
| 2 | Flip Lightning | Withdraw, then ID4 on the other bank |
| 3 | Power bank USB-A | ID5 |
| 4 | Mac/hub | Unknown |
| 5 | A1357 → Mac, quick swap × 10 | Every swap withdrawn, zero poller misses |
| 6 | A1357 unplug/replug as fast as possible × 10 | Same, plus the shortest human VBUS gap |

Pass:

- correct class on every attach;
- no false withdrawal from 176;
- 100 % IRQ withdrawals and zero poller-only detections;
- edge → verified readback well below the shortest gap (target ≤ 30 ms);
- every transfer over 30 ms reported.

Send back: the per-row log, the histogram, and the shortest gap.

---

<a id="ch-04"></a>
## CH-04: BC1.2 and CDP detection (computers and generic chargers)

- **Devices**: J71b; J71t/J71s and J72x after CH-02.
- **Why**: Computers, hubs and generic chargers carry no Apple divider, so
  Linux holds them at 500 mA. iPadOS has a port-type path for them:
  - `sdp`/`cdp`/`dcp`/`cp`, 500–1500 mA ([brickid.md](brickid.md#port-type-table));
  - it reported 1500 mA on a Mac USB-A hub that macOS accounted as 500 mA.

  Two leads exist:
  - **Firmware**: Tristar modes 4/5 with `0x03` excitation, thresholds
    257/258 mV or a 326–899 mV window, depending on an upstream class.
  - **Claim (PR #8, iPad 5)**:
    - primary D− 326–899 mV → secondary;
    - secondary D+ < 326 mV → CDP, otherwise DCP;
    - both mean 1500 mA.

  Our one J71b mode 4/5 attempt lost the source about 20 ms after the first
  excitation.
- **Equipment**:
  - a generic USB-A wall charger sold as BC1.2 DCP, or one with D+/D−
    shorted;
  - a computer or hub port documented as a charging downstream port (CDP);
  - a plain computer USB-A port (SDP);
  - the A1357 as a control;
  - one Lightning to USB-A cable;
  - optionally a USB tester that names the detected protocol
    (*inferred*: many show "DCP" or "Apple 2.4A").
- **Time**: A 1 day, B 2 h. **Risk**: B medium. `0x03` excitation may
  disturb the source; the limit stays at the default.

Steps:

A. Offline (21H461 or the device's own kernelcache):
   1. Confirm the mode 4/5 sequences in
      [tristar-cbtl1610.md](tristar-cbtl1610.md#port-type-modes-45-same-monitor-bc12-style)
      at `…615d758`–`…615d778`.
   2. Trace `…5e68a50` and PR #8's anchors `…5e68c48`, `…5e68de8`: which
      mode is primary and which secondary, which thresholds apply to which
      upstream class, and where the upstream class comes from
      (`…5e6da30`, slot 688).
   3. Write the decision as a table.

B. Live (only after A agrees with the claim or corrects it):
   1. At the default limit, with the USB gadget unused (Wi-Fi only).
   2. For each source and both plug orientations:
      - one mode-4 and one mode-5 sample on each bank;
      - `0x18a` before and after, PMU events, mux restore result.
   3. Abort the source on any 72/73 or status change. Do not retry more
      than once.

Send back: the decision table (A) and source × orientation × bank ×
mode → mV, events, `0x18a` (B).

---

<a id="ch-05"></a>
## CH-05: USB Power Delivery

- **Devices**: J71b; any device with a jailbroken iOS for part A.
- **Why**:
  - The kernelcache has an IDBus PD path (PDO parsing, confirmed RDO).
  - Its transport references were found under `AppleCBTL1614Device`, not
    CBTL1610.
  - On J71b, iPadOS listed no PD service and reported 5000 mV on a 140 W PD
    charger.
  - If iPadOS never negotiates PD on a device, Linux does not need PD there.
- **Equipment**: a PD charger (any USB-C charger of 18 W or more), an Apple
  USB-C to Lightning cable, a USB-C tester that shows VBUS voltage and PD
  messages if available, jailbroken iOS.
- **Time**: A 1 h, B 1–2 days. **Risk**: none.

Steps:

A. iOS:
   1. Charge from the PD charger with the battery below 80 %.
   2. Record `IOAccessoryUSBChargingVoltage`, `IOAccessoryUSBCurrentLimit`,
      and whether any class with `PD` in its name exists
      ([CH-13](#ch-13)).
   3. Read the tester: does VBUS ever go above 5 V? Does the tester see a
      PD contract?

B. Offline: does the device's concrete port-controller class instantiate
   `IOAccessoryIDBusPD`? Follow its constructor and vtable, not string
   proximity.

Send back: VBUS voltage seen, PD contract yes/no and its PDOs from the
tester, the iOS values, and the class trace.

---

<a id="ch-06"></a>
## CH-06: Suspend behaviour

- **Devices**: J71b first; any device with working suspend.
- **Why**: The prototype drops to the 100 mA handoff before suspend and
  forgets the source, because sleep can hide a swap. Unknowns:
  - Does `0xa03` survive suspend and resume?
  - Does a plug or unplug while suspended wake the device or leave events
    pending?
  - What does the attached host do (USB suspend)?
  - USB 2.0 suspend current is far below 500 mA. PR #6 granted 500 mA to a
    suspended host and the review rejected it.
- **Prerequisite**: suspend and resume work on the device without charging
  code. On J71b they did not work reliably, so this is blocked there.
- **Equipment**: the device, a Mac, an Apple brick.
- **Time**: 2 h. **Risk**: low at the default limit.

Steps:

1. Owner at default. Read `0xa03`, `0x18a`, the gauge. Suspend.
2. Resume. Read the same values and the event block once (as the owner).
3. Suspend, unplug, wait 10 s, plug back, resume. Read again; note whether
   the plug event woke the device.
4. Attached to a Mac: put the Mac to sleep while the device is awake. Record
   gadget state, `0x18a`, the event bits, and the gauge current.

Send back: values per step, wake yes/no, event bits.

---

<a id="ch-07"></a>
## CH-07: Live USB-C Rp changes

- **Devices**: J71b; any board with USB-C over Lightning.
- **Why**:
  - A USB-C source may lower its Rp advertisement while staying attached.
    The sink must follow within 60 ms (tSinkAdj).
  - On J71b, CC is visible only when the mux selects it; one acquisition
    takes about 23 ms and a burst renewal took 45 ms.
  - We do not know whether any PMU or Tristar event fires on an Rp change,
    or whether real sources do this.
- **Equipment**:
  - a USB-C charger with two or more USB-C ports. *Inferred*: many lower the
    first port's advertisement when a second device is plugged in;
  - a second phone or device to plug in;
  - an Apple USB-C to Lightning cable;
  - a USB-C tester that shows CC voltage or the Rp level.
- **Time**: 1 h. **Risk**: low. The limit stays at 425 mA; the mux is
  switched repeatedly.

Steps:

1. Confirm with the tester alone that the charger's first-port Rp drops
   when a second device is plugged into it.
2. Connect the iPad. Owner at default. Wi-Fi transport.
3. Sample CC on the orientation-selected lane 20 times at 100 ms
   intervals. Restore the mux after each sample.
4. While sampling, plug the second device into the charger, then remove it.
5. Record the CC mV time series, every PMU event with timestamps, and
   `0x0c`.

Send back: the series, whether any event or status change coincided with
the Rp change, and the tester's reading.

---

<a id="ch-08"></a>
## CH-08: iPhone 7 / 7 Plus port controller identification

- **Devices**: D10, D101 (iPhone 7); D11, D111 (iPhone 7 Plus). Part A needs
  no device.
- **Why**:
  - Paul's D111 tree charges through an SN2400 at i2c1 `0x75`. It measures
    VBUS/IBUS and folds back on droop, without identifying the source. D11
    is untested; D10/D101 have stub trees only.
  - Which Lightning port controller these phones have (Tristar, Hydra,
    other) and how they learn source current is unknown.
  - Paul's tree describes the T8010 iPhones' PMIC as a D2333 (patch
    "Add D2333 PMIC GPIO support"), not the iPads' D2365. Its charger and
    event registers may differ from J71b's (*inferred*).
- **Equipment**: A: IPSW for D10/D101/D11/D111 and an ADT/device-tree
  extractor. B: a D111 or D11 running the stack, a Mac, an Apple brick, a
  USB-C charger.
- **Time**: A 2 h, B 1 h. **Risk**: none (reads only).

Steps:

A. From each device tree, list every node whose `compatible` or name
   contains `tristar`, `hydra`, `cbtl`, `ths`, `bellatrix`, `sn2400`,
   `charger`, `pmu` or `gas-gauge`. For each node record:
   - I²C bus and `reg`, `interrupts`;
   - `function-*` property names;
   - charger node limits (`usb-input-limit-max` and the like), and whether
     a calibration property exists (not its bytes).

B. D11/D111 with Paul's SN2400 driver: for each source, record the
   charger's `input_current_limit` after it settles, `voltage_now`,
   `current_now`, and how long the ramp took.

Send back: the node table per board, plus the B table.

---

<a id="ch-09"></a>
## CH-09: Does attaching a source reset the input limit?

- **Devices**: J71b; J71t/J71s.
- **Why**:
  - On J71b, VBUS loss does not reset `0xa03`. `0x1c` stayed for 4.9 s
    until software restored it.
  - Claim (PR #8, iPad 5 captures): `0xa03` changed `0x20` → `0x02` within
    26 ms of an attach, without software.
  - If attach resets the limit on J71b too, a stale grant cannot survive a
    replug. It can still survive while unplugged, and on a swap without a
    VBUS gap. Either way, never rely on it.
- **Equipment**: J71b, an Apple brick.
- **Time**: 30 min. **Risk**: low (425 mA ≤ USB 2.0 500 mA).

Steps:

1. A test owner that holds `0x1c` across detach. It must not restore
   `0x02` on `0x18a` = 0.
2. Unplug. Read `0xa03` at the detach edge and after 1 s.
3. Plug back in. Read `0xa03` at the attach edge (IRQ thread) and at 10 ms,
   100 ms and 1 s.
4. Repeat 5 times, with gaps of 1 s and 10 s.

Send back: `0xa03` values with timestamps per repetition.

---

<a id="ch-10"></a>
## CH-10: Lightning orientation bit and lane selection

> **Result on J71b (2026-10-28):** the bit follows the plug. After a flip
> the driver still sampled the CC lane (1731 then 1736 mV, other lane 646
> then 948 mV) and granted 3 A both times
> ([measurements](measurements.md#driver-session-apple_tristar_charger-2026-10-28)).
> Still open on other CBTL1610 boards.

- **Devices**: J71b; any CBTL1610 board.
- **Why**: Tristar `0x0c` bit 6 chooses the CC lane.
  - In status-only reads it stayed 0 across physical rotations
    (`0xa3` every time).
  - The prototype later identified the 140 W charger as 3 A in both
    orientations.
  - If bit 6 does not follow the plug, the driver samples the wrong lane.
    On the 140 W charger the other lane read 872 mV, which would classify
    as 1.5 A.
- **Equipment**: J71b, Apple USB-C to Lightning cable with a tape mark,
  the 140 W charger and the 20 W power bank.
- **Time**: 1 h. **Risk**: low (detector only).

Steps, for each source and each orientation A/B, unplugging fully in between:

1. Read `0x0c` before and after reading the capability frame.
2. Decode the frame; record the route tag order (`usb0/usbc` or `usbc/usb0`).
3. Sample both CC lanes (`0xc0` and `0x0c`) and restore after each.
4. Repeat steps 1–3 two minutes later without touching the cable.

Send back: source × orientation → `0x0c` before and after, tag order, both
lane voltages, and whether the 2-minute repeat changed anything.

---

<a id="ch-11"></a>
## CH-11: More Apple bricks (BrickID table coverage)

- **Devices**: J71b; J71t/J71s and J72x once their detector runs.
- **Why**: Only ID4 (A1357, 2100 mA) and ID5 (power bank, 2400 mA) are
  measured. PR #8 claims the 5 W brick reads 1000 mA and the 12 W brick
  2400 mA.
- **Equipment**: any Apple USB-A brick (5 W, 10 W, 12 W, older iPod or iPad
  bricks); third-party chargers that advertise Apple modes; one Lightning
  to USB-A cable.
- **Time**: 5 min per brick. **Risk**: low (detector only, default limit).

Steps: for each brick and both orientations, run the two-bank detector and
record D+/D− mV on both banks and the decoded ID.

Send back: brick model number (from the label), label rating, orientation,
bank values, decoded ID.

---

<a id="ch-12"></a>
## CH-12: USB-C grant policy

- **Devices**: J71b (iOS and Linux), then any device with USB-C over
  Lightning.
- **Why**:
  - iPadOS chose 1500 mA on the Apple 140 W charger, whose CC reads in the
    3 A band, and 2400 mA on a 20 W power bank on the same cable.
  - Firmware caps a 3 A Type-C item to 1500 mA when the prior charging-port
    type is 0.
  - The prototype granted 2400 mA on the 140 W charger.
  - One Mac USB-C sample read 961 mV (1.5 A); the prototype record says a
    Mac USB-C port gave 3 A.
  - We need the rule before shipping Type-C grants above 1500 mA.
- **Equipment**:
  - J71b with jailbroken iOS and Linux;
  - Apple 140 W (or another Apple USB-C charger), 18–20 W USB-C chargers,
    a USB-C power bank;
  - Mac USB-C ports: note which port and whether anything else is plugged
    in;
  - an Apple USB-C to Lightning cable.
- **Time**: A 1 h, B 1 day, C 1 h. **Risk**: none to low.

Steps:

A. iOS ([CH-13](#ch-13)): `IOAccessoryUSBCurrentLimit` and `PortType` per
   source.

B. Offline: trace where the prior charging-port type (item + `0x2c`, passed
   at `…5e6df8c`) comes from on a USB-C cable, and when it is 0.

C. Linux, detector only: CC mV on the selected lane per source, same cable
   and orientation as A.

Send back: source → iOS limit, CC mV, and the rule from B.

---

<a id="ch-13"></a>
## CH-13: iOS reference observation

- **Devices**: any device that can run a jailbroken iOS. J71b used
  palera1n on iPadOS 17.7. *Inferred*: palera1n supports the A9–A11 devices
  listed here.
- **Why**: iOS's chosen current per source is the reference answer for
  Linux on that device.
- **Equipment**: the device on jailbroken iOS with SSH, the sources from the
  entry that sent you here, and a Mac to read the host's allocation.
- **Time**: 1 h. **Risk**: none (read-only on iOS).

Steps:

1. Dump the IORegistry with properties. For example, `ioreg -l -w0` if your
   jailbreak provides it (*inferred*), or build the read-only observer
   described in [ios-firmware.md](ios-firmware.md#runtime-observation-on-ipados).
2. Filter to these classes:
   - `AppleTriStarBuiltIn` or the device's equivalent;
   - `AppleCBTL16*` / Bellatrix / Hydra;
   - `AppleARMPMUCharger`;
   - `IOAccessory*`;
   - anything with `PD` in its name.
3. Keep only these keys:
   - `IOAccessoryUSBCurrentLimit`, `IOAccessoryUSBCurrentLimitBase`;
   - `IOAccessoryUSBChargingVoltage`;
   - `PortType`, `IOAccessoryDetect`;
   - `AdapterInfo`, `BestAdapterIndex`.

   Drop everything containing `serial`, `address`, `ecid`, `unique`,
   `productid`, `vendorid`, `deviceid`, `chipid` or `accessoryid`.
4. One snapshot per source, with the battery below 90 %. Also take one
   snapshot after unplugging and one after replugging.
5. On a Mac host, note the USB current the Mac allocated to the device.
   *Inferred*: System Information → USB shows it.

Send back: source → kept keys, plus the Mac allocation.

---

<a id="ch-14"></a>
## CH-14: Actual input current per code

- **Devices**: J71b; J171/J172 (Paul's 1425 mA grant); D11/D111 (compare the
  SN2400 IBUS reading).
- **Why**:
  - Every current in this guide is nominal. It is either the formula
    `code × 12.5 + 75` or a driver reading.
  - iPadOS uses a per-unit calibration, so the real draw per code is
    unknown.
- **Equipment**: an inline USB meter (USB-A or USB-C, matching the source)
  between the charger and the cable, with a resolution of 10 mA or better.
- **Time**: 1 h. **Risk**: none at codes up to the default. Codes above the
  default only after CH-03 passes on that device, and only on a source
  rated above the code.

Steps:

1. Screen off or fixed brightness, battery 20–60 %, pack below 35 °C.
2. Hold each code for 60 s and record the meter's current and voltage plus
   the gauge `current_now`. Codes:
   - `0x02` and the default on every device;
   - after CH-03 on J71b: `0x32`, `0x4a`, `0x72`, `0x9a`, `0xba`, on the
     Apple 140 W charger or the 20 W power bank.
3. Return to the default.

Send back: code → meter mA, meter V, gauge mA, pack temperature.

---

## Per-device menu

<a id="menu-j71b"></a>
**iPad 6 Wi-Fi (J71b).** The reference board.
- Do [CH-03](#ch-03) first.
- Then [CH-10](#ch-10), [CH-12](#ch-12), [CH-11](#ch-11), [CH-09](#ch-09),
  [CH-14](#ch-14), [CH-04](#ch-04), [CH-07](#ch-07).
- With a jailbroken iOS: [CH-13](#ch-13), [CH-05](#ch-05).
- [CH-06](#ch-06) once suspend works.

<a id="menu-j72b"></a>
**iPad 6 cellular (J72b).** *Inferred*: same charging hardware as J71b.
Verify with:
- [CH-00](#ch-00);
- [CH-02](#ch-02) part A as a diff against J71b's values in
  [README.md](README.md#components-on-j71b).

Then [CH-11](#ch-11) and [CH-13](#ch-13).

<a id="menu-ipad5"></a>
**iPad 5 (J71t, J72t, J71s, J72s).**
- [CH-00](#ch-00), then [CH-02](#ch-02) (wiring, events).
- [CH-09](#ch-09) (the attach reset was claimed on this device).
- [CH-13](#ch-13), [CH-11](#ch-11).
- [CH-04](#ch-04) once CH-02 matches J71b.
- [CH-03](#ch-03) before any grant.

<a id="menu-ipad7"></a>
**iPad 7 (J171, J172).**
- [CH-00](#ch-00).
- [CH-01](#ch-01) (Bellatrix source current, the main gap).
- [CH-13](#ch-13), [CH-14](#ch-14) (measure what the 1425 mA grant draws
  from each source).
- [CH-06](#ch-06) once suspend works.

<a id="menu-iphone7"></a>
**iPhone 7 (D10, D101).** [CH-08](#ch-08) part A, needing no device. Then
[CH-00](#ch-00) and [CH-13](#ch-13).

<a id="menu-iphone7plus"></a>
**iPhone 7 Plus (D11, D111).**
- [CH-08](#ch-08) A and B.
- [CH-00](#ch-00).
- [CH-14](#ch-14): meter against the SN2400 `current_now` and the ramp.
- [CH-13](#ch-13).

<a id="menu-air2"></a>
**iPad Air 2 (J81, J82; A8X, D2207 PMIC).** Outside this guide's measured
scope. [CH-00](#ch-00), [CH-13](#ch-13) and [CH-14](#ch-14) apply. The D2207
charger reads VBUS through the PMIC ADC; see
`notes/2026-10-28/ipad-air2-analysis.md`.

<a id="menu-offline"></a>
**No device, just an IPSW.** Static work that unblocks others:
- [CH-08](#ch-08) A;
- [CH-01](#ch-01) B;
- [CH-04](#ch-04) A;
- [CH-05](#ch-05) B;
- [CH-12](#ch-12) B.

A11 devices (iPhone 8, iPhone X) have no charging material in our sources
yet. Start with [CH-00](#ch-00), [CH-08](#ch-08) A (same node search) and
[CH-13](#ch-13).

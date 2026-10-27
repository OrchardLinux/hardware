# J71b charging lease

The default is the existing root-only 15-second lease, with input code `0x1c`
(425 mA), the original battery windows, and verified `0x02` (100 mA) handoff.
Module load performs no register writes. `j71b-input/online` keeps its existing
read-only physical-presence semantics. The runtime renewer still renews every
5 seconds and writes zero on exit. A source worker never renews that deadline.

## Experimental source-aware mode

**Not tested on J71b hardware.** linux-a9 round 7, from which this is adapted,
also has no device validation at preparation time. Keep the default for release.
Use the matching patched kernel, module and final DTB together. After qualifying
the particular unit's Tristar revision and privately staging its calibration,
opt in with a root-owned modprobe configuration:

```text
options j71b_charge_policy source_aware=1
```

The setting is read-only after load. In the normal image, place that line in
`/etc/modprobe.d/j71b-source-aware.conf` before a fresh boot. The module is
normally loaded by `phase2-hardware`; changing modprobe configuration after it
has loaded does not switch modes. The service/helper paths remain unchanged.
No userspace source guessing or extra charging service is needed.

The calibration file is `/lib/firmware/apple/j71b-CBAT.bin`: the **24-byte
`usb-input-limit-calibration` property from this J71b unit**, read from its
existing iOS IORegistry, not a whole raw syscfg partition. It contains five
little-endian signed Q16 values and version 1. The driver validates the record,
its slopes, each computed ceiling, and strictly increasing input codes. No
J71t record or guessed code table is accepted as board evidence. Do not commit
or distribute per-unit files. This change does not add a private-data fetcher.

Missing/invalid calibration, missing DT wiring, an occupied I2C child, an
unsupported topology or a revision other than `0x8a` keeps the 425 mA lease.
Only `0x8a` is presently protocol-qualified by linux-a9's reference. J71b's
public ADT proves CBTL1610, **not its live revision**, so a different revision
needs separate reverse engineering; there is no revision-bypass parameter.

Computers identified by a DWC2 reset/enumeration use the public board ceiling
(`apple,usb-input-limit-max-microamp`, 2400000 on J71b). This is an opt-in
reproduction of Apple's board policy, **not a claim that a USB host negotiated
or can supply 2.4 A**. Enumeration accounting, descriptors and recovery USB
identity are unchanged. A proven BC1.2 DCP/CDP retains its classification when
USB traffic appears. An Apple divider can be superseded by computer evidence.

The divider table, row-major by D+ then D− bands, is:

| D+ / D− | 1500–2199 mV | 2200–2879 mV | 2880–4000 mV |
| --- | ---: | ---: | ---: |
| 1500–2199 mV | 500 mA | 1000 mA | 1000 mA |
| 2200–2879 mV | 2100 mA | 2400 mA | 3000 mA |
| 2880–4000 mV | 1000 mA | 1000 mA | 1000 mA |

Every allowance is capped by the board maximum. The calibrated ladder is
500/1000/2100/2400 mA, at most one upward rung per second; BC1.2's 1500 mA
allowance consequently selects 1000 mA. Unknown sources retain 425 mA.
Printed brick wattage does not override the sampled signature. The nominal
500 mA rung uses a 400 mA battery-current cap; higher rungs cap battery current
at 2500 mA, below the required `0xf4` hardware cap's 2600 mA low six bits.
Actual pack current also depends on system load, state of charge and the PMIC.

## Ownership and safety boundary

- Renewals and worker changes retain the original battery requirements:
  present, SOC 15–95%, 5–40 °C, 3.2–4.35 V, current within ±3 A, and successful
  property reads. Timeout, invalid PMIC state and changed full cap byte refuse
  charging. Source mode also refuses termination when it has a source budget.
- `0xa03` is never adopted from an unexplained elevated handoff. Before source
  mode starts, `0xa08` and `0xa00[1]` are snapshotted at module load; later
  unexplained changes refuse ownership. Source mode requires input enabled.
  Charging writes are **only `0xa08` and `0xa03`**. This implementation never
  writes `0xa00`, and never uses input suspend to bracket a nonzero change.
  Nonzero transitions use battery-setting then input-setting order. Handoff
  reduces input to `0x02` before restoring/verifying the saved `0xa08` byte.
- The **one non-charging PMIC write** is `0x4000 = 1` to start the pin ADC,
  only inside the opted-in, calibrated policy during an active lease. The
  J71b 21H461 iPad7,5_6 kernelcache confirms the gated ADC at
  `fffffff006b7c3f4`, trigger at `fffffff006b85e0c`, and two-byte result at
  `fffffff006b85e40`. The analog monitor is `fffffff00615d57c`. The public
  ADT's `adcV(1, 0x10000)` selects this pin-monitor path, not a VBUS channel.
- Analog work holds the DWC2 role hardware mutex, then the policy mutex.
  Only J71b's fixed peripheral topology is accepted; a role-switch topology
  is refused. There is no USB host/accessory/ID-bus support in this port.
  Gadget events are checked before/after samples, and activity invalidates
  the whole sweep. Tristar `0x03` is cleared before `0x01` is restored and
  both are checked. ADC uncertainty remains poisoned until module teardown;
  a replug cannot make an uncertain conversion fresh. Use a fresh boot after
  an ADC timeout rather than treating module reload as hardware recovery.
- Physical epoch advances only after a successful Tristar `0x0c` read with
  neither VBUS bit set. Detach restores handoff, revokes the old lease, clears
  source/ramp/back-off state and requires a fresh root renewal. USB reset,
  suspend and transport failures do not advance it. There is no user override
  that can exceed the source allowance. A short pulse missed by every 250 ms
  poll can still escape detection.
- An observed lower owned `0xa03`, with battery setting and input-enable bit
  unchanged, caps the policy at the next lower supported rung (or below).
  It never immediately raises an observed setting below the calibrated floor.
  Hold is 60 seconds, then at most one upward rung per minute. **J71b's D2365
  iOS backend reports no input limiting** (`fffffff006b85348`, constant false;
  measured input current at `fffffff006b85230` is unsupported). **This is not
  voltage-triggered AICL:** no verified VBUS voltage, droop threshold or input
  current is available. A weak port may shut down without lowering `0xa03`;
  a complete collapse/replug can restart the ramp. Qualification is required.
- Zero, expiry, guard failure, PM prepare and exit restore owned fields.
  Readbacks and three bounded retries establish handoff. Failed restoration
  latches `restore_failed`; only explicit zero after checked handoff/route
  repair clears it. PM parks/cancels both workers outside the mutex, repairs
  Tristar routing and aborts suspend on any unverified handoff. PM post writes
  nothing and replays no lease. Suspend can hide a complete source swap, so
  old identity is discarded; analog stays blocked until a measured empty VBUS
  interval or fresh USB evidence. Replug visibly if a brick stays at 425 mA
  after resume. Module unload cannot report failure to its caller and is not
  a recovery procedure for a restoration fault.

`/sys/module/j71b_charge_policy/parameters/source_state` exposes the opt-in and
calibration status, revision, epoch, source allowance, board ceiling, nominal
rung, live input/battery codes, analog-sample validity, adaptive cap/back-off
reason, errors, lease state, route restoration flags, ADC poison and PM gate.
`vbus_mv=unavailable` is deliberate. `sleep_parked` and `restore_failed` retain
their existing paths. Codes are programmed limits, not measured charge rate.

## Offline validation

Run the normal Python unit suite from an installed/development package:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

`tests/test_j71b_charging.py` compiles and executes the actual policy/detector
functions with scripted regmap, I2C, gadget events, battery and PM calls under
ASan/UBSan, including real pthread ADC-vs-PM and zero-vs-PM interleavings.
PM also serializes zero's unlocked cancellation/route-repair window, so a
racing zero cannot perform I2C operations after the PM notifier has finished.
The harness uses a synthetic calibration record and never accesses a device.
Hardware qualification and rollback instructions are in
[`test-plan.md`](test-plan.md).

The detector, calibration and adaptive policy are adapted from Berkay
Kösebay's linux-a9 charge7 (`a8aa6fea363394f3d21f2eef7525998884b4cf3f`),
reconstructed from Apple's iOS charging paths. Kernel/platform groundwork:
Pauli1Go/HoolockLinux; port ownership, lease/runtime and J71b reference:
OrchardLinux's existing J71b implementation. See the repository `NOTICE`.

# USB-C sources over Lightning

Applies when the capability frame decodes a `usbc` route tag (an Apple USB-C
to Lightning cable). The CC line of the USB-C end reaches the iPad on the
Lightning lane opposite the data pair. The CBTL1610 can route it to the PMIC
ADC.

## Service chain (firmware, 21H461)

`determineUSBCharger` (`…5e6df78`) constructs `IOAccessoryUSBTypeCCurrentDetect`
(ctor `…5ebb828`, vtable `…6dd5008`, init via slot `0x540` → `…5ebb8b0`). It
passes the previously detected charging-port type (item + `0x2c`) into the
service at + `0xd0` (`…5e6df8c`, `…5ebb8c4`, `…5ebb92c`).

| Layer | Address |
|---|---|
| Sampling task; reads `IOAccessoryOrientation` | `…5ebbc80` (via `…5ef2b68`) |
| Public selector: orientation 2 → 0, otherwise → 2 | `…5ebbcf0`–`…5ebbcf8` |
| Gated analog wrapper (provider slot `0x860`) | `…5ef713c` |
| Concrete BuiltIn `0x860` | `…6e580f0` + `0x860` → `…614c2d8` |
| Selector translation table `(2,4,3,5,2,2,0xffff)` | `…5531990` |
| CBTL1610 direct monitor | `…615da44` |
| Voltage → class | Type-C item ctor `…5e69690` |
| Prior-port cap | `…5ebbd4c`–`…5ebbd80` |

## Classification

| CC on selected lane | Class |
|---|---|
| < 660 mV | No enhanced Type-C current (default USB power; **not** "0 mA" and not automatically 500 mA) |
| 660–1229 mV | 1500 mA |
| ≥ 1230 mV | 3000 mA |

Orchard's offline model adds 50 mV exclusion bands around 660 and 1230. The
prototype uses the bare thresholds.

## iPadOS policy

This is more than the voltage class:

- **Prior-port cap**: a 3000 mA item becomes 1500 mA if and only if the
  prior charging-port type field is 0.
- **Eligibility** (`…5ebc110`). The service runs only when all of these hold:
  - route derivation succeeds and contains `usbc`;
  - a compatible port is acquired;
  - the existing supplier current is ≤ 1500 mA and below the supplier
    ceiling;
  - a busy-state check passes;
  - the `OverrideBase`, `FullyCharged`, 500 mA-boundary and CK-profile
    (`18 fe 09 a1 00 00`) checks pass.
- The device ceiling (2400 mA on J71b) applies on top.

iOS observation on J71b, same Lightning to USB-C cable:

| Source | iPadOS limit | Linux CC sample | Notes |
|---|---|---|---|
| Apple 140 W | 1500 mA | 1741 mV (3 A band); other lane 872 mV | Matches the prior-port cap if prior type = 0 (inferred) |
| 20 W power bank (label: USB-C 5 V 3 A, total 5 V 2.4 A) | 2400 mA | 1780 mV, later 1766, 1770, 1792 mV | |
| Mac, direct USB-C | 2400 mA (macOS sent a 2400 mA configuration override) | 961 mV (1.5 A band) | macOS override is a host-side Apple mechanism |

Prototype (hardware commit `11073db`) on the same kind of sources:

- 140 W in both orientations → 3 A class → granted 2400 mA;
- power bank → granted 2400 mA;
- "Mac USB-C port (3 A)".

Two disagreements with the table above are open:

- iPadOS uses 1500 mA on the 140 W charger, the prototype 2400 mA. Both are
  within the Type-C 3 A advertisement; iPadOS is applying its own policy.
- The earlier Mac sample read 961 mV (1.5 A), while the prototype record
  says 3 A. It may have been a different Mac port or a different state.
  The source is not recorded precisely enough to tell.

See [CH-12](experiments.md#ch-12).

## Orientation

Status `0x0c` bit 6 gives the orientation that selects the lane:
`0xc0` for orientation 1, `0x0c` for orientation 2. Firmware decodes it this
way (`…615c498`: bit 7 gate, bit 6 orientation).

Observed on the power bank:

- `0x0c` = `0xa3` attached, `0x24` unplugged, `0xa3` after reinserting the
  plug the other way round;
- a second rotation also gave `0xa3`;
- a physically flipped CC sample still reported orientation 1 and read
  1756 mV, so the crossover was rejected.

The prototype, after reading the capability frame, identified the 140 W
charger as 3 A in both orientations.

Firmware does not compare against a previous physical position. It uses the
cached `IOAccessoryOrientation`, which discovery publishes. Whether bit 6
follows the physical plug, and when it refreshes, is open
([CH-10](experiments.md#ch-10)). Reading the other lane as well (the
prototype logs it) gives a cross-check: on the 140 W charger the other lane
read 872 mV.

## Rp changes without detach

USB Type-C Cable and Connector Specification R2.0 (August 2019), §4.6.2.1 and
table 4-29: a sink adjusts to an Rp change within tSinkAdj, at most 60 ms.

On J71b the CC level is visible only while the mux selects it. No interrupt
for an Rp change is known (inferred from the event captures: only attach and
detach were exercised).

Measured costs of one CC acquisition:

- 23.1 ms from trigger to qualified with fast guards;
- a second renewal in a burst took 45.4 ms;
- each sample switches the mux, and mux activity has stalled USB data on
  this device.

Periodic re-sampling within 60 ms, including withdrawal, is not shown to be
possible with the current I²C path ([CH-07](experiments.md#ch-07)). The
`i2c-pasemi` transfer timeout alone is 100 ms.

Practical options, inferred:

- grant Type-C current only up to a level that is safe for every Rp the
  source could fall back to;
- show that the sources in question do not change Rp while attached;
- find an interrupt source for CC changes.

PR #8 re-sampled every 5 s. The review rejected that.

## USB PD

The 21H461 kernelcache has an IDBus PD path:

- `IOAccessoryIDBusPD` receives `kAIDPDDataMsg_Source_Capabilities` with raw
  PDOs (handler `…5e78aa4`, parsing `…5e791e4`).
- `IOAccessoryIDBusPDSupplyData` decodes fixed PDOs as voltage bits 19:10 ×
  50 mV and current bits 9:0 × 10 mA.
- Confirmed-RDO handling is at `…5e78908`.
- `IOAccessoryPowerSourceItemPD` is built from the selected profile.

The concrete transport references (`IOAccessoryIDBusTransportTaskComplete`,
`InternalAuth`) were found in `AppleCBTL1614Device`, not in CBTL1610.

On J71b iPadOS:

- no `IOAccessoryIDBusPD` or `IOAccessoryPowerSourceItemPD` service was
  listed;
- `IOAccessoryUSBChargingVoltage` stayed 5000 mV with the 140 W PD charger.

A private OSObject need not appear in the registry, so this does not prove
PD is unused. See [CH-05](experiments.md#ch-05).

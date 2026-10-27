# J71b charging lease

The default remains the root-only 15-second lease at input code `0x1c` (425 mA),
with the original battery guards and checked `0x02` (100 mA) handoff. Loading
makes no register write. Default operation makes no Tristar or ADC write.
`j71b-input/online` and the runtime paths are unchanged. The renewer runs every
five seconds; source work cannot renew the lease.

## Experimental source-aware mode

**J71b hardware is untested.** The source design is linux-a9 charge10, which
passed iPad 5 device tests for USB-A/USB-C computers and USB-C wall charging.
That is J71t evidence, not J71b qualification. Keep the default for release
until the [maintainer test plan](test-plan.md) passes on J71b.

Use the matching patched kernel, external module and final DTB. Opt in before
a fresh boot with a root-owned modprobe configuration:

```text
options j71b_charge_policy source_aware=1
```

The setting is read-only after load. The normal image loads the module from
`phase2-hardware`; changing modprobe configuration afterwards does not switch
modes. No extra service or userspace source guessing is needed.

Source mode requires `/lib/firmware/apple/j71b-CBAT.bin`, the **24-byte
`usb-input-limit-calibration` property from this J71b unit**, obtained by a
read-only iOS IORegistry export. This is not a whole syscfg partition. The
record contains five signed little-endian Q16 values and version 1; slopes,
computed limits and increasing calibrated codes are validated. Do not use
J71t's calibration, and never commit or distribute per-unit records.

Missing/invalid calibration, missing wiring, an occupied I2C child, an
unsupported topology or a revision other than `0x8a` retains the 425 mA lease.
J71b's public ADT proves CBTL1610, **not its live revision**. Only reference
revision `0x8a` is supported; a different revision needs separate qualification.

## Sources and lifetime

A genuine DWC2 enumeration/gadget data session establishes a computer,
**superseding every earlier BC1.2, divider or CC result**. The source policy
then uses the public `usb-input-limit-max` board ceiling (2400000 µA on J71b),
independently of descriptor current accounting. This is Apple's opt-in board
policy, **not negotiated proof that an arbitrary PC/hub can supply 2.4 A**.
USB identity, descriptors, endpoints and gadget current accounting are unchanged.
A short real session is latched until its source epoch ends. Idle state,
line/ID events and suspend notifications alone neither invent a computer nor
cancel pin sampling; the end of a real session still cancels an in-flight sample.

The Apple divider table, row-major by D+ then D− voltage bands, is:

| D+ / D− | 1500–2199 mV | 2200–2879 mV | 2880–4000 mV |
| --- | ---: | ---: | ---: |
| 1500–2199 mV | 500 mA | 1000 mA | 1000 mA |
| 2200–2879 mV | 2100 mA | 2400 mA | 3000 mA |
| 2880–4000 mV | 1000 mA | 1000 mA | 1000 mA |

BC primary D− 326–899 mV requests secondary detection. **Secondary D+ below
326 mV means CDP; 326 mV or above means DCP.** Both give a 1500 mA allowance.
The old round-7 labels were reversed; J71b's own iOS name table confirms this
correction. Passive D+/D− readings are different from the primary/secondary
measurements and are exposed separately.

The supported Apple USB-C→Lightning ID `10 09 08 00 00 00` qualifies a CC
measurement, not a fixed current grant. CC is D+ on the **opposite Lightning
lane pair**, never a VBUS measurement. At most three samples, at least 20 ms
apart, must produce two equal advertisements: ≤659 mV gives 0, 660–1229 gives
1500 mA, ≥1230 gives 3000 mA. A 3 A advertisement requires separately verified
DCP/CDP evidence; otherwise it is capped at 1.5 A. CC is refreshed every five
seconds while idle. A failed CC refresh removes its elevation and retains the
proven BC baseline. No USB-PD negotiation or currents added together are implied.

Every allowance is capped by the board ceiling. The calibrated ladder is
500/1000/2100/2400 mA, at most one upward rung per second. A 1500 mA source
uses the 1000 mA rung. Unknown sources retain 425 mA. The 500 mA rung caps pack
current at 400 mA; higher rungs use 2500 mA, below the required cap byte's
2600 mA low six bits. Actual pack current also depends on load, SOC and the PMIC.

A new source epoch follows an observed empty VBUS interval, a changed **fresh,
CRC-checked** digital ID, or an identified→device/empty connector edge (plug or
orientation change), even with VBUS continuously high. It restores handoff,
revokes the old lease and clears source/adaptive state. A first ID refines the
same epoch and retains BC evidence; it does not duplicate the connector-edge
epoch. Root must renew for the new source. Polling can still miss a same-ID
swap with no observed VBUS, plug or orientation edge.

Only bounded GetID is added, under the same role/policy locks as sampling.
It writes volatile Tristar TX/engine registers `0x5d..0x61` with request `0x74`
and HOSTID `0x0002`, checks TX readback before GO, and accepts only a fresh
8-byte `0x75` response with CRC. It never writes the RX window, resets or
reorients the cable, changes the AID switch, sends power requests, enters host
mode, or requests OTP/ESN data. A reset hold refuses GetID. An uncertain GO or
timeout poisons further ID requests until teardown; use a fresh boot rather
than treating replug/reload as proof that the hardware is idle.

## Ownership and PM

The original battery windows remain: present, SOC **15–95%**, **5–40 °C**,
**3.2–4.35 V**, current within **±3 A**, with successful reads. Timeout, invalid
PMIC state and a changed full `0xf4` cap byte refuse the lease. Source mode
also refuses termination with a source budget. The iPad 5 device captures at
100% do not relax J71b's SOC guard; replays use captured source measurements
with safe synthetic battery telemetry.

Source mode snapshots the handoff `0xa08` and `0xa00[1]` at load and refuses
later unexplained ownership changes. Charging writes are **only `0xa08` and
`0xa03`**, both checked. This port never writes `0xa00`. Nonzero changes use
battery-setting then input-setting order, without an input-suspend ritual.
Handoff reduces input to `0x02`, then restores/verifies the saved battery byte.

An owned `0x02` input setting, with battery setting and input-enable matching,
is an attach-default reset during the **15-second** settling window after an
epoch/first ownership. The worker repairs the current calibrated step without
false back-off, including while its next ramp step is pending. Other lower
owned settings can lower the adaptive rung, hold 60 seconds and retry at most
one rung/minute. The driver writes that rung's calibrated code, rather than
leaving a nominal 500 mA step programmed at 100 mA. Repairs occur at worker
execution; failed I/O or battery/source guards can prevent a successful write.

**This is not voltage-triggered AICL.** J71b iOS D2365's input-limiting callback
is constant false (`fffffff006b85348`), and measured input current is unsupported
(`fffffff006b85230`). An input-setting change has unknown electrical cause. A
weak source can sag/shut down without changing `0xa03`, or collapse/reconnect
and restart the ramp. No voltage/droop guarantee follows from this policy.

The **one non-charging PMIC write** is the owner-approved `0x4000=1` ADC trigger,
only in the opted-in, calibrated, active lease. Source work holds DWC2's role
hardware lock, then `policy_lock`; only a fixed peripheral topology is accepted.
Known active USB excludes routing; real session generations, strict VBUS and
orientation are checked around samples. Zero mux is valid in either orientation.
Tristar `0x03` is cleared before saved `0x01` routing is restored and checked.
ADC poison survives replug; uncertain completion cannot become a fresh sample.

Zero, expiry, guard failure, PM prepare and exit restore owned fields. Checked
retries establish handoff; failed restoration latches `restore_failed`, cleared
only by explicit zero after verified recovery. PM parks and cancels both
workers outside the policy mutex. It also serializes zero's cancellation/repair
window with `renewal_lock`, repairs routing, verifies handoff, and vetoes suspend
on uncertainty. PM post performs no register write or lease replay. Sleep can
hide a complete swap, so old identity is discarded; analog waits for a visible
unplug or fresh host evidence. A brick can need replug after resume. Module
unload cannot report restoration failure and is not a recovery procedure.

`source_state` is read-only: source/epoch/reason/phase, input and battery codes,
BC/CC voltages, board/nominal/adaptive ceilings, attach-reset and USB-idle/data
counters, detector stage/error, fresh-ID state, route/ADC/PM gates. It explicitly
reports `vbus_mv=unavailable`. Existing `sleep_parked` and `restore_failed` paths
are retained. Programmed limits are not measured source or battery current.

## Evidence and offline checks

J71b **21H461 iPad7,5_6** unslid iOS addresses independently confirm:

| Path | Address |
| --- | --- |
| Type-C sampling / qualification clamp | `fffffff005ebbc80` |
| Type-C voltage thresholds | `fffffff005e69690` |
| Gated pin wrapper / selector map | `fffffff005ef713c` / `fffffff00614c2d8`, table `fffffff005531990` |
| Opposite DX lane monitor, accepting zero mux | `fffffff00615da44` |
| BC secondary / enum name serializer and table | `fffffff005e68c48` / `fffffff005e68de8`, table `fffffff006dc3750` |
| Bounded ID command TX/readback/GO engine | `fffffff006155f70` |
| ADC gate / trigger / result | `fffffff006b7c3f4` / `fffffff006b85e0c` / `fffffff006b85e40` |

Kernelcache SHA-256:
`5c0822a78a10191b56edd468f100b331b0e86bcafa3fdcc5d974dadc427dbec9`.
No decompilation, Apple firmware or private calibration is distributed.

Run the normal suite from an installed/development package:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
```

The actual-C ASan/UBSan harness includes the kernel event helper, scripted
regmap/I2C/ADC, synthetic calibration, captured source sequences, fault/restore
cases and real pthread ADC-vs-PM/zero-vs-PM interleavings. The fixture records
hashes of 226/255/322 iPad 5 capture rows from rounds 8/9/10. It tests BC→ID→USB,
wall BC→CC, the 26 ms attach reset, and high-VBUS fast swaps without claiming
that J71b hardware has run them.

Adapted from Berkay Kösebay's linux-a9 charge7–10, final source design
`a18b0e2ee18def3a2d6090e9dc8edd73beb3abd8`. Kernel/platform groundwork:
Pauli1Go/HoolockLinux; J71b port, lease, runtime and PM reference: OrchardLinux.
See `NOTICE` and the [maintainer test plan](test-plan.md).

# J71b maintainer qualification (not run)

No J71b is available to the author. linux-a9 charge7 is also device-untested at
preparation time. These are proposed maintainer actions, not device results.
Keep the shipped 425 mA default until this matrix passes on a J71b.

## Preparation and baseline

1. Retain a known-good complete kernel/module/DTB bundle and a way to reboot
   it. Use a local root so unplugging the test USB cable cannot destroy an
   NBD root session. Do not write factory storage, NVRAM or boot firmware.
2. Record the exact hardware commit, kernel release, config/patch hashes,
   iPad product/board, cable model and charger/host identity. Capture
   `dmesg`, battery properties and `/sys/module/j71b_charge_policy/parameters/`.
   Do not publish per-unit calibration, MAC addresses or serial identifiers.
3. With no opt-in setting, load the matching module at verified `0xa03=0x02`,
   full cap `0xa0c=0xf4`. Check no writes at load. Renew using the existing
   helper/service; confirm `guarded_425mA`, input `0x1c`, and **no ADC trigger
   (`0x4000`), Tristar writes or battery-current-setting changes**.
4. Stop the renewer: zero must succeed, `restore_failed=N`, input `0x02`.
   Record this unit's handoff battery code before enabling source mode.
5. Read the live Tristar revision without writing it. Only `0x8a` is currently
   supported. Obtain this unit's 24-byte `usb-input-limit-calibration` through
   a read-only iOS IORegistry export, review it locally, and stage it privately
   as `/lib/firmware/apple/j71b-CBAT.bin`. Never use the iPad 5 record. A
   missing, invalid or unsupported record/revision must retain the 425 mA lease
   and perform no ADC writes; test that refusal first.
6. Add `options j71b_charge_policy source_aware=1` to a root-owned modprobe
   file and boot the matching experimental bundle afresh. Capture the same
   baseline before starting the renewer. If an ADC timeout occurs, retain the
   log and return to a fresh boot; unplug does not clear `adc_poisoned`.

## Source matrix

For each row, start from an observed unplug (both `0x0c` VBUS bits clear),
then attach and renew. Capture at least 90 seconds at idle; qualify a longer
run with representative workload and temperatures after the basic pass.
Use a suitable external meter for actual VBUS/current and port shutdown.
Ceilings below are nominal; the **unit's validated calibration** determines
`0xa03`, so do not assert J71t's raw codes on a J71b.

| Source | Expected opted-in policy |
| --- | --- |
| Mac USB port + Lightning cable | DWC2 reset/enumeration identifies computer; 500 → 1000 → 2100 → 2400 mA, at least 1 s per upward step, capped by the DT board maximum. This is Apple's board policy, not negotiated proof of port capacity. |
| Apple 12 W brick | Sampled divider, commonly 2400 mA; actual measured signature/table wins. The table's 3000 mA entry must cap at 2400. |
| Apple 10 W brick | Sampled divider, commonly 2100 mA; confirm D+/D− classification rather than label-based selection. |
| Apple 5 W brick | Sampled divider, commonly 1000 mA; never exceed its detected ceiling. A 500 mA table result must stay on the 500 mA rung. |
| USB-C charger + USB-C-to-Lightning cable | No PD stack is added. Valid Apple divider uses its table ceiling; valid BC1.2 DCP/CDP gives 1500 mA allowance and the 1000 mA calibrated rung. Unknown stays 425 mA. Record charger and cable because the cable can affect the signature. |
| Weak USB port / hub | A computer still selects the board-policy ceiling. There is **no verified voltage AICL protection**. Measure externally; an observed lower owned PMIC input code must back off and hold for 60 s, then retry at most one rung/minute. Overcurrent shutdown, repeated collapse/restart, an unchanged code during harmful droop, or unstable USB is a qualification failure. Stop the test; never raise a raw code to provoke a shutdown. |

Use a bounded sampler, for example (run on the maintainer's test J71b):

```sh
for n in $(seq 1 360); do
    date -u +%FT%TZ
    cat /sys/module/j71b_charge_policy/parameters/source_state
    cat /sys/module/j71b_charge_policy/parameters/sleep_parked
    cat /sys/module/j71b_charge_policy/parameters/restore_failed
    for key in present capacity temp voltage_now current_now status; do
        printf '%s=' "$key"
        cat "/sys/class/power_supply/battery/$key"
    done
    sleep 1
done
```

Record `dmesg` alongside this output. Record USB reset/configuration/suspend
and link continuity, source epoch, revision, sample validity, nominal and live
input codes, battery-current code, source/platform/adaptive ceilings, route
flags, error fields, back-off reason, SOC/temperature/voltage/current. Label
current sign and units. Input ceilings and pack current are different values.

## Transition, fault and PM checks

- Mac → brick → Mac, including a detach/reattach shorter than the root helper's
  5 s period but long enough to appear in the 250 ms detector poll. Epoch must
  change, old lease/classification/adaptive state must clear, and a new source
  must not inherit the old computer ceiling. USB reset/suspend/resume with
  physical VBUS present must retain the epoch and source identity.
- While configured/active, no analog routing or ADC trigger is permitted.
  Kernel event generation must cancel each interrupted ADC sweep; no partial
  Apple or BC result is published. Observe unchanged recovery descriptors,
  USB identity and endpoint behavior.
- **The sole non-charging PMIC write is `0x4000=1`**, in an opted-in, calibrated,
  active, traffic-free lease under the role hardware lock. Tristar writes are
  only the iOS monitor routing (`0x01`) and BC pulse (`0x03`). After every
  sweep/failure, `0x03=0` must be checked before the saved `0x01` route is
  restored and checked. Charging writes are only `0xa08` and `0xa03`; this
  port never writes `0xa00`. Do not inject faults by changing raw registers
  on a live tablet; use the offline harness or a reviewed mock/fault provider.
- Zero the lease, kill only the renewer PID you started (including a test
  without its EXIT trap), and stop the service. Without renewal, handoff must
  be restored when the 15-second watchdog runs: input `0x02`, saved battery
  code, no route pending and no automatic replay. Releasing a reference is
  not restoration evidence. Check that source polling never extends the lease.
- Safe successful suspend/resume: stop the source worker before I2C suspend,
  verify input/battery/route handoff, then resume with no write/replayed lease
  in PM post. The next root renewal must recheck all guards. After sleep,
  unobserved source swaps are treated as unknown; analog needs an observed
  unplug or fresh host event. A brick can require a visible replug.
- Offline injected restoration failure must veto suspend, unpark the failing
  notifier and latch `restore_failed`. An aborted suspend by another notifier
  must also leave the driver unparked with no lease replay. Renewal must refuse
  until explicit zero proves every owned field and route restored.
  The offline zero-vs-PM interleaving must also keep PM excluded throughout
  zero's cancellation/route-repair window outside the policy mutex.
- Exercise battery-property failures and guard boundaries in the harness.
  On hardware use naturally occurring pack conditions; do not heat the pack,
  manipulate its protection, or fabricate raw fuel-gauge values. SOC outside
  15–95%, temperature outside 5–40 °C, voltage outside 3.2–4.35 V, current
  outside ±3 A, timeout, termination in source mode and cap-byte changes must
  stop elevation and restore owned fields.

Pass requires the expected ceilings and restore evidence, stable USB and
thermals, no unexplained register adoption, and no unsafe weak-source behavior.
Neither a successful build nor a healthy battery reading proves weak-port
protection. D2365's iOS input-limiting callback reports false; observed PMIC
reductions must be called reductions with unknown cause, never proven droop.

## Rollback

Stop the renewer/service. Require a successful explicit zero write, input
`0x02`, original battery code, cleared route flags and `restore_failed=N`.
Remove the modprobe opt-in and private test calibration, then reboot the
retained known-good complete bundle. A restoration failure is a failed test;
do not force suspend or keep testing higher currents. Do not use module unload
as proof of restoration. No test requires syscfg/NVRAM/boot-firmware writes.

## Evidence to attach to the eventual PR

- [ ] J71b unit/revision and privately checked calibration provenance (no blob).
- [ ] Default-mode no-ADC/425 mA and all source rows above.
- [ ] Replug, USB PM/reset, lease expiry, PM success and PM veto evidence.
- [ ] Weak-port external-meter qualification, including stated limitations.
- [ ] linux-a9 iPad 5 round-7 device evidence: **pending; insert owner results**.

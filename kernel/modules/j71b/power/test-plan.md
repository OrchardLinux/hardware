# J71b maintainer qualification (not run)

J71b hardware is unavailable to the author. linux-a9 charge10 passed iPad 5
source tests, including USB-C computer BC→ID→enumeration and USB-C wall CC.
Those captures supply replay inputs; they do not qualify J71b electrical/PM
behavior. Keep the 425 mA default until this matrix passes on J71b.

## Preparation and default

1. Retain a known-good complete kernel/module/DTB bundle and recovery path.
   Use a local root: unplugging a cable carrying an NBD root invalidates that
   session. No test needs syscfg, NVRAM or boot-firmware writes.
2. Record hardware commit, kernel release/config/patch hashes, product/board,
   cable and source identity, and local calibration provenance. Do not publish
   private calibration, MACs or serial identifiers. Use SOC **15–95%** and the
   existing pack safety windows for source tests; iPad 5 evidence at 100% does
   not relax the J71b guard.
3. With no opt-in, verify load makes no register write. Renew using the existing
   helper: `guarded_425mA`, `0xa03=0x1c`, unchanged battery setting, **no ADC,
   Tristar route, or ID-engine writes**. Zero/stop must restore `0x02` and leave
   `restore_failed=N`. Record the original battery-setting byte.
4. Read this unit's live Tristar revision; only `0x8a` is supported. Privately
   stage its own 24-byte IORegistry `usb-input-limit-calibration` as
   `/lib/firmware/apple/j71b-CBAT.bin`. No J71t data or whole syscfg partition.
   Missing/invalid calibration, unsupported revision or topology must retain
   425 mA and perform no ADC/ID writes.
5. Add `options j71b_charge_policy source_aware=1` to a root-owned modprobe
   file before booting a fresh matching bundle. Loading still writes nothing.
   An ADC/ID timeout must not be cleared by pretending unplug/reload proves
   idle hardware; retain the log and return to a fresh known-good boot.

## Source matrix

Observe unplug first (both Tristar VBUS bits clear), attach, and renew. Log at
least 90 seconds at idle, followed by an appropriate sustained workload/thermal
run after the basic pass. Use an external meter for actual VBUS/current and
port shutdown. Nominal budgets map through the **unit's** calibration; J71t
raw codes are not J71b expected values.

| Source | Expected opt-in behavior |
| --- | --- |
| Mac USB-A→Lightning, directly or through a USB-C adapter | Real enumeration → computer, board ceiling; calibrated 500→1000→2100→2400 mA, at least 1 s per upward step. |
| Mac USB-C computer + Apple USB-C→Lightning | Early BC CDP/1500 mA may select 1000 mA; first ID retains BC; enumeration supersedes BC/CC and reaches the board's 2400 mA ceiling. Networking must stay up. |
| Apple 12 W brick | Sampled divider, commonly 2400 mA; measured signature wins. A 3000 mA table result must cap at 2400. |
| Apple 10 W / 5 W bricks | Sampled divider, commonly 2100 / 1000 mA. Verify the signature; printed wattage is not a source grant. A 500 mA signature stays on its calibrated rung. |
| USB-C wall charger or Mac USB-C power adapter + Apple USB-C→Lightning | Fresh supported ID permits opposite-lane CC. BC DCP/CDP qualifies ≥1230 mV as 3 A, capped at 2400. 660–1229 mV gives 1500 mA (1000 rung); unqualified 3 A also caps at 1500. Unknown stays 425. No PD stack is added. |
| Weak PC/hub/port | Computer policy still uses the board ceiling: **no verified voltage AICL**. Measure externally. Repeated collapse, harmful droop with unchanged input code or unstable USB fails qualification. Stop; do not force a higher raw code. |

Host board policy is not a negotiated 2.4 A grant or evidence of arbitrary
port capacity. Programmed input limits and measured pack current are different;
pack current can be stale after replug or negative under system load.

Use a bounded logger on the maintainer's J71b, alongside `dmesg`:

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

Record source/epoch/reason/phase, raw Tristar status, source/board/nominal/
adaptive ceilings, live input and battery codes, attach-reset count, BC primary
and secondary versus passive pin voltages, CC samples, ID validity/errors,
USB total/idle/data-generation observations, route flags, ADC/ID poison and
all battery safety values. Retain USB reset/configuration/suspend continuity.

## Required replay and transition checks

- **Zero mux:** `0x01=0x00` must permit passive/BC sampling in both Lightning
  orientations and restore exactly zero. Selected non-USB accessory routing
  must be refused; the other nibble must remain untouched.
- **BC→ID→USB:** replay the captured 594/39 mV primary/secondary pair: CDP,
  then first `10 09 08` ID retaining BC, then real enumeration superseding all
  analog entitlements. A short real session is latched; no further ADC starts.
  Idle gadget/line/ID notifications during every conversion must not cancel
  a valid result or invent a computer. A real intervening session must cancel.
- **Wall BC→CC:** replay captured 667/668 mV (DCP), first ID, then 1624 mV CC.
  Two matching advertisements within at most three reads, at least 20 ms
  apart, allow 3 A capped by the board. Test one unsettled first read. Refresh
  CC without repeating proven BC pulses; uncertain refresh falls back to BC.
- **Attach reset:** replay the captured 26 ms `0x20→0x02` change. With matching
  owned battery setting and input-enable bit, within the 15-second settling
  window, repair the current calibrated rung with no false adaptive back-off.
  Repeat before the next ramp deadline: pending ramp must not skip repair.
  Outside that window, unexplained lower input chooses/holds a lower calibrated
  rung; live code must match it after successful worker execution. Failed
  I/O/guards are not proof a repair succeeded. There is no droop interpretation.
- **Fast swap:** Mac→wall→Mac while sampled VBUS remains high. Changed valid
  ID or identified→device/empty plug/orientation edge advances the epoch,
  restores handoff, clears computer/BC/CC/adaptive state and revokes the lease.
  First ID after that edge must not create a duplicate epoch. Repeated identical
  IDs, ordinary reset/suspend and transport failures must not invent epochs.
  A same-ID swap with no observable edge can still be missed.
- **GetID envelope:** only active, calibrated opt-in leases may issue bounded
  GetID. TX readback mismatch must prevent GO; old RX without fresh completion,
  bad header/CRC and an uncertain timeout must not qualify a cable. No second
  GO after an uncertain request. Only volatile `0x5d..0x61` are written by
  identification; never RX, reset/reorient, AID, power, OTP or ESN operations.

## Lease, faults and PM

- The **one non-charging PMIC write is `0x4000=1`** under the role hardware
  lock in an opted-in, calibrated, traffic-free active lease. Routing writes
  are only Tristar `0x01` and BC `0x03`; clear/check `0x03` before restoring/
  checking the original `0x01`. Charging writes are only PMIC `0xa08`/`0xa03`;
  this port never writes `0xa00`. ID-engine writes above are Tristar, not PMIC.
- Zero, stop the service and kill only a renewer PID you started (also test
  without its exit trap). Without renewal, the 15-second watchdog must restore
  input `0x02`, saved battery byte and clear route flags; work must not extend
  the lease or replay it. An unexplained elevated handoff must be refused.
- Successful suspend: park/cancel both workers before I2C suspension, finish
  bounded identification, restore/verify charge fields and routing. PM post
  makes no register write or lease replay. Fresh renewal rechecks all guards.
  Sleep can hide a source swap, so a wall source can need visible replug.
- Offline restoration failure must veto PM, unpark the failing notifier and
  retain `restore_failed`. Another notifier's aborted suspend must also unpark
  with no lease replay. Explicit zero clears the barrier only after verified
  recovery. Real pthread ADC-vs-PM and zero-vs-PM cancellation-window tests must
  pass. Do not inject faults by changing raw registers on a live tablet.
- Use the offline harness for battery read failures/guard boundaries. On
  hardware use natural conditions; do not heat the pack, manipulate protection
  or fabricate gauge values. Preserve SOC 15–95%, 5–40 °C, 3.2–4.35 V and ±3 A.

## Rollback and evidence

Stop the renewer. Require successful explicit zero, input `0x02`, the original
battery byte, cleared route flags and `restore_failed=N`. Remove the modprobe
opt-in/private test record, then reboot the retained known-good complete bundle.
A restoration failure is a failed test, not permission to force suspend or
continue higher-current tests. Module unload is not proof of restoration.

Attach J71b revision/private calibration provenance (no blob), default-mode
no-write evidence, source/transition matrix, expiry and PM success/veto logs,
and external weak-port qualification. iPad 5 charge10 success is recorded in
linux-a9 `docs/periph/logs/charge10-20261027/RESULT.md`; J71b evidence is **pending**.

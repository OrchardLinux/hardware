# Review: Orchard's J71b kernel delta against the new stack

Inputs:
- **ours**: Hoolock `6831bc70` plus the current `patches/j71b/*`, the kernel J71b has run;
- **new**: torvalds `v7.3-rc1` + `patches/hoolock` + `patches/import/pauli1go`.

Method: a per-file 3-way merge (base Hoolock / ours / new), then a direct diff of
ours against new for every file both sides changed. Our code is not assumed correct.

Our patches touch 53 files:

| Class | Files | Meaning |
|---|---|---|
| Same as Paul | 15 | Byte-identical to Paul's; nothing of ours to carry |
| Clean on top | 4 | Kconfig/Makefile edits |
| Orchard only | 16 | J71b DTs, readiness driver, NVMe policy header, Wi-Fi |
| Conflict | 15 | Both changed the same code; reviewed hunk by hunk below |

## Decision: no bring-up scaffolding in the final stack

The production J71b DTB is a chain of bring-up layers (`devbase` → `storage-ro` →
`pcie-dev` → `sio-dev` → `readiness`), several marked diagnostics-only. Touch runs on
out-of-tree driver copies in `kernel/modules/j71b/touch/` (`apple-sio-core`,
`apple-sio-dma`, a 77 KB `apple_z2` copy). Charging relies on a userspace lease
service. Paul's J172 is near-identical A10 hardware with an in-tree DT and drivers.

Decided (2026-10-28): J71b is re-platformed on Paul's model instead of carrying
this forward. The final stack has:
- no diagnostics-only DT layers, `J71B_*` gate markers or machine-gated fences in
  shared drivers;
- no out-of-tree driver copies;
- no userspace charging lease: charging is kernel `power_supply` drivers.

What survives from our delta: the `shared/` fixes below, plus J71b board data
(DT, compatibles, quirks) where the hardware really differs from J172.

Storage safety moves from kernel fences to test order: RAM boot → read-only
mount → read-write on the Linux partition → installer.

In the tables, **j71b/** now means "scaffolding: drop; re-derive any real board
difference while porting J71b to the J172 model".

## Verdicts

`shared/` = general fix, kept as its own commit and offered upstream.
`j71b/` = J71b-specific, carried for track A. `take new` = ours was older/worse.

### NVMe (`drivers/nvme/host`)
| Hunk | Verdict | Notes |
|---|---|---|
| ANS: release FlatDMA slot before unmapping pages; keep mapped if release fails | **shared/** | Paul unmaps first and only warns on failure, so the controller could DMA into freed pages. Offer to Paul. |
| `CONTEXT_ANALYSIS` removed, `__guarded_by`/`__must_hold`/`__context_unsafe` dropped | **take new** | Workaround; analysis needs Clang ≥ 23 (toolchain has 19) |
| quirk-parse `-EINVAL`, `nvme_disable_ctrl()` on IRQ failure, rate-limited warning, `data_race()` | **take new** | Mainline fixes missing from our older base |
| J71b fence: `j71b_ro_test`/`j71b_rw`, RO allowlist, fail→hold, DMA stop/drain, no FLR/subsystem reset, module pin, RO namespace, no retries (`pci.c`, `core.c`, `j71b-readonly-policy.h`) | **j71b/** | Production relies on it (`nvme j71b_rw=1`). One patch on the new `pci.c` keeping the annotations. Re-evaluate in B against J172's unfenced RW path. |

### PCIe H9P (`pcie-apple-h9p.c`, Kconfig)
| Hunk | Verdict | Notes |
|---|---|---|
| `tristate` + `.remove`; NVMMU tables via `dma_alloc_attrs` (not devres) | **shared/** | J71b loads PCIe as a module after the storage fence. Tables retained because hardware may still fetch them: **flag:** leaked per reload. |
| Firmware region must not overlap System RAM | **j71b/** now | General safety value; propose an ungated version to Paul |
| Discovery probe, `enumeration-only`/`readonly-storage`, root-port gate, single attempt | **j71b/** | In the production path via `storage-ro.dts`; redesign in B |

### SmartIO (`apple-sio-core.c`, Kconfig, Makefile)
| Hunk | Verdict | Notes |
|---|---|---|
| IRQ requested disabled (`IRQF_NO_AUTOEN`), enabled after firmware validation, disabled on stop, freed before teardown | **shared/** | Race fix: a stale IRQ could start shared-region setup or run during teardown |
| J71b firmware SHA-256/size gate, `SIO_GATE_*` logs, `CRYPTO_LIB_SHA256` | **j71b/** | Orchard's firmware recipe already verifies the hash; drop in B |
| Module name `apple-sio-core` vs Paul's `apple-sio` | **take new** | Update the J71b runtime references. Also removes a name clash with the out-of-tree `apple-sio-core.ko`. |
| Abridged Kconfig help texts | **take new** | |

### DART (`apple-dart.c`)
| Hunk | Verdict | Notes |
|---|---|---|
| Controller-wide `apple,dma-range` window, per-pagesize domain mask | **j71b/** | Hoolock's per-master window in `iommus` args is the upstream mechanism; move J71b to it in B. **Flag:** mask change unexplained. |
| **Bug in new stack:** `apple_dart_s5l8960x_irq()` dereferences `dev_iommu_priv_get(dart->dev)`, which is NULL for the DART itself | **shared/** fix | Any translation fault on an S5L8960X-type DART (T8010 included) oopses in IRQ context. From Hoolock `090-dart` patch 0005. Report to Nick. |

### Battery gauge (`bq27xxx_battery_hdq.c`)
| Hunk | Verdict | Notes |
|---|---|---|
| Error on missing HDQ response, drvdata ordering | **take new** | Paul generalized our fix |
| `ti,bq27540-hdq` (J71b's compatible) missing from Paul's table | **shared/** | Add a BQ27541-profile entry **without** `cache_only`: Paul's 30 s cache would stale `current_now` for the charging guards. **Flag:** check against his cache-only design. |

### UART, serdev, clocks, DMA, pwrctrl
| Hunk | Verdict | Notes |
|---|---|---|
| `samsung_tty.c` T8010 baud rate | **take new** | Ours is an earlier draft of Paul's commit |
| `serdev.h`, `serdev-ttyport.c`, pwrctrl Kconfig/Makefile, clk/dma Kconfig | **take new** | Formatting/abridged copies |

### Wi-Fi (`brcmfmac`, Paul untouched)
| Hunk | Verdict | Notes |
|---|---|---|
| `core.c`: don't cancel uninitialized reset work when firmware lookup fails before attach | **shared/** | General bug fix; linux-wireless candidate |
| `pcie.c`: `brcmf_fw_crashed()` only if the device came up | **shared/** | **Flag:** confirm generality |
| Exact OTP-tuple NVRAM (`BRCMF_FW_REQF_EXACT`), MAC required, RX `DMA_BIDIRECTIONAL`, `J71B_WIFI_*` logs | **j71b/** | In B compare with Paul's NVMEM calibration on the same BCM4355C1; find why J71b needs bidirectional RX |

### Bluetooth (`hci_bcm.c`)
| Hunk | Verdict | Notes |
|---|---|---|
| Local HCI reset, LE adv fix-up disabled | **take new** | Workarounds for the older base; new stack has `btbcm_reset` and the quirk |
| `apple,j71b-bcm4355c1-bt` device data | **j71b/** | Compare with Paul's `j172` entry in B |

### Other
| Hunk | Verdict | Notes |
|---|---|---|
| pinctrl `irq_set_wake` for J71b s2idle | **j71b/** | Suspend experiment; proper wake support in B |
| J71b DTs, `apple_j71b_readiness` driver, input Kconfig/Makefile | **j71b/** | |

## New-stack behavior changes J71b must be validated against (step 4)

- `i2c-pasemi`: Paul's T8010 init/STOP/recovery now applies to J71b's I²C bus
  (PMIC, TriStar, charging).
- In-tree `gpio-apple-d2365`, while J71b loads an out-of-tree module of the same
  name (`kernel/modules/j71b/gpio`). Pick one and avoid a module-name clash.
- New Kconfig symbols with `default ARCH_APPLE` will flip on. Diff the resolved
  `.config` against today's.
- Production DTB hash check in `devicetree/j71b/build` will change (expected).

## Upstream reports to send (later)

- Nick Chan (Hoolock): DART S5L8960X fault-handler NULL dereference.
- Paul Praschl: ANS slot release ordering; SmartIO IRQ lifecycle; H9P System-RAM
  overlap check; bq27540 entry and the cache-only question.
- linux-wireless: brcmfmac cancel of uninitialized reset work.

## Status of shared fixes (now patches/drivers/<subsystem>/)

Committed on the stack (verified to apply; not yet compiled):
1. iommu: apple-dart: no master-config dereference in the S5L8960X fault handler
   (found by our own code review; PR #6 happens to change the same line)
2. nvme-pci: apple-ans: release the FlatDMA slot before unmapping its pages
3. soc: apple: sio: take RX interrupts only while the coprocessor runs
4. power: supply: bq27xxx_hdq: BQ27540 (iPad 5/6), live reads; cache-only stays BQ27545
5. wifi: brcmfmac: don't cancel never-initialized reset work
6. wifi: brcmfmac: Apple OTP NVRAM selection without antenna SKU (replaces J71b's
   exact-OTP code; same filenames, no fallback files packaged)

Revised after the no-scaffolding decision:
- H9P module support: dropped. Only J71b's fence boot order needed it.
- brcmfmac crash-report guard: dropped. Mainline brcmf_fw_crashed() already
  returns early without drvr.

## Unverified sources

hardware#6 (J71t, brkosebay) and hardware#8 (charging, same author, same flow
ported to J71b) are untrusted references. Nothing from them is adopted
without our own verification. #8's charging policy had known flaws: computer
→ 2.4 A from a USB session flag, and stale USB-C advertisement refresh.

The J71b/J71t CBTL1610 charging source is built from our research and the
device-tested `j71b_adaptive_charge` module as a `power_supply` source for
Paul's D2365 charger. `apple_tristar_power.c` from #6 is reference only.
J71t integration gets the same hunk-by-hunk review as our own delta.

## Touch (`apple_z2.c`)

Our out-of-tree `kernel/modules/j71b/touch/j71b_z2.c` is Paul's `apple_z2.c` plus
J71b additions (~500 changed lines), booted through a separate
`verified-bootstrap.h` path. Plan: a `J71B` variant in Paul's driver, next to
J172/D11/D111, using his normal boot path.

Real J71b differences (keep, as variant code or DT):
- Power-on: reset held; analog rail, 10 ms, LDO, 1 ms, clock, 15 ms.
- Boot: 4-byte zero preamble at 500 kHz under reset; reset released, not
  pulsed; one firmware-ready IRQ (J172: two); display-sync pins untouched;
  no init ACK (`apple,z2-no-init-ack`); 16-bit words from 17 bytes
  (`apple,z2-bpw16-min-len = <17>`).
- Runtime ("generation 1" firmware):
  - packet length payload+5 (min. 16);
  - data phase sends `EB, tag, 1` plus checksum, not `A5` filler;
  - separate TX/RX buffers, CPHA off and 500 kHz above 16 bytes;
  - handles empty-queue, no-data and control replies;
  - framed second phase for feature reads; surface descriptor phase byte may be 1;
  - reports 0x73/0x9d optional, so no J172 BF writes.
- Timing: CS setup/inactive 1 ms, hold 0. Level-low IRQ at runtime, up to 4
  reports drained per IRQ. RX buffer 8 KiB.
- DT: `firmware-name`, touch size (hard-coded 1535x2047 today).

Scaffolding (drop): `enable_runtime` and counter parameters, `J71B_*` logs and
dev_dbg→dev_info changes, single-attempt latches, probe gating on machine,
compatible, SPI speed and SIO state, whole-firmware preflight (the Orchard recipe
verifies hashes), SIO payload counters, the readiness DT and driver,
`quiesce-api.h`.

Risk: this combined path has never run on J71b. Touch is the first item of the
staged on-device check.

## Build status (2026-10-28)

- Full J71b build on the new stack (torvalds v7.3-rc1 + hoolock + pauli1go +
  shared): Image.gz, 55 modules and DTB, no compiler warnings.
- Recompiled with `W=1`, no warnings: every file touched by the shared fixes,
  the FlatDMA hand-port (`pci.c`, `pci-apple-ans.c`), the three `tsl2583`
  hand-ports and the J71b touch variant (`apple_z2.c`).
- Config: `TOUCHSCREEN_APPLE_Z2=m` (in-tree touch) and `TSL2583=m` added.
- Not established: runtime behavior. Touch especially (never run in this
  combined form) is the first item of the staged on-device check.

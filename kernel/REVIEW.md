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

## Two tracks

The production J71b DTB is a chain of bring-up layers (`devbase` → `storage-ro` →
`pcie-dev` → `sio-dev` → `readiness`), several marked diagnostics-only. Touch runs on
out-of-tree driver copies in `kernel/modules/j71b/touch/` (`apple-sio-core`,
`apple-sio-dma`, a 77 KB `apple_z2` copy). Paul's J172 is near-identical A10
hardware with an in-tree DT and drivers.

- **A — migration, no behavior change:** J71b's gates and DTs move into
  `patches/j71b/` so the step 4 boot matches today's kernel.
- **B — re-platform J71b on Paul's DT/driver model** (like J172), subsystem by
  subsystem with on-device validation; storage writes last. Most `j71b/` items
  below are expected to shrink or disappear there.

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

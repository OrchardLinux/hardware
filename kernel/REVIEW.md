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
| Exact OTP-tuple NVRAM (`BRCMF_FW_REQF_EXACT`), MAC required, RX `DMA_BIDIRECTIONAL`, `J71B_WIFI_*` logs | **j71b/** | In B compare with Paul's NVMEM calibration on the same BCM4355C1; find why J71b needs bidirectional RX. **2026-10-28, new stack without it:** firmware boots and scans, and association starts. At each EAPOL retry (1 s apart) the DART reports a READ FAULT on stream 0 (a mapped but non-readable page), and afterwards every firmware command times out. Stale-TLB is the other explanation; not yet confirmed which. Untested: whether J172 (same BCM4355C1) has the same problem. |

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

Tracked in [NOTIFY.md](NOTIFY.md).

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

hardware#6 (J71t, brkosebay) and hardware#8 (charging, the same flow
ported to J71b) are unverified references. Nothing from them is adopted
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

## Device trees (2026-10-28)

`patches/devices/t8010/0001` follows mainline's layout. `t8010-ipad6.dtsi`
describes the whole iPad 6 board (J71b/J72b include it directly), and
`t8010-ipad7.dtsi` includes it and overrides only what differs on the
iPad 7. The bring-up layer `t8010-j7xb.dtsi` is gone. The WLAN UART
platform-control block is a disabled SoC node in `t8010.dtsi` (it was
added from the board file through `&{/soc}`). The PMIC GPIO controller
sits inside the PMIC node. `t8010-j71b.dts`/`t8010-j72b.dts` hold the
per-model Wi-Fi board type and touch firmware name.

Checked with an FDT-level comparison against the DTBs that booted on
2026-10-28: J71b, J72b, J171 and J172 are identical apart from label names.
D10, D11, D101, D111 and N112 only gain the disabled WLAN UART node.

Earlier check, against the first version of this patch: Verified by decompiling all 104 Apple DTBs before and after:
- 100 unchanged byte for byte;
- J171/J172: only a `wifi` label symbol and a `wifi0` alias added, otherwise
  identical apart from phandle numbering;
- J71b compared with the old production DTB (d05ad7b7, overlay chain): Wi-Fi
  regulator, PMIC GPIO, PCIe port 3, Wi-Fi node, platform control, Bluetooth
  and HDQ nodes identical; SPI3 pins identical. Intended changes: Wi-Fi board
  type, gauge monitored-battery, SIO DMA for touch, touch compatible and
  properties (4 MHz, firmware, size; CS timing now in the driver).

Driver additions for these DTs: `drivers/bluetooth` J71b BCM4355C1 entry
(its validated settings; whether J172's would also work is untested) and the
`apple,j71b-touchscreen` binding.

Open: J72b needs its own touch firmware (`apple/j72b-fwstart.z2fw`) and Wi-Fi
NVRAM names from its IPSW (firmware recipe, CLI phase). The iPad 6 battery
capacity (8827 mAh) is the published figure, not read from the gauge.
`t8010.dtsi` carries a pre-existing `simple_bus_reg` warning (bus@20c000000).

## Mainline-practice fixes for the imported pauli1go layer (backlog)

Orchard maintains this code now; Paul is not expected to. Backlogged
2026-10-28: nothing here blocks a device. Fix in place, in the relevant
layer, and verify on J71b plus FDT and build checks for the iPad 7/iPhone 7
boards we can't run. Triggers:
- Wi-Fi calibration mechanism: decide during the m1n1 audit.
- Touch firmware names: settle with the firmware recipe (build
  pipeline/CLI).
- Everything else: when preparing upstream submissions.

- **apple_z2 tuning in DT:** `apple,z2-bpw16-min-len`, `apple,z2-no-init-ack`
  and the SPI chip-select timings are driver knowledge per controller
  variant. They belong in the per-compatible match data, not in DT
  properties. `power-ldo-gpios`/`power-ana-gpios` are rails, so model them
  as `*-supply` (fixed regulators) unless the sequencing needs GPIOs.
  The driver also needs `spi_device_id` entries (the kernel warns for
  `apple,j71b-touchscreen`).
- **Board compatible on an SoC block:** `pcie-apple-h9p` matches
  `apple,j172-pcie` to enable a bridge quirk. Mainline keys board quirks
  on a property describing the hardware behavior, or on the SoC
  compatible plus match data, not on a board-named controller compatible.
- **H9P per-port bring-up booleans** (`apple,link-retry-after-power-cycle`,
  `apple,hardware-trained-phy-lane`, `apple,endpoint-power-after-link-setup`,
  `apple,inherited-perst-before-link-setup`): check which describe the
  hardware and which are driver policy. Policy moves into the driver.
- **`apple,antenna-sku = "XX"`** on the iPad 7 is a placeholder. Mobile
  devices have no antenna SKU (`patches/drivers/wifi/0002` handles that),
  so drop it once J172 NVRAM selection by OTP is confirmed by file names.
- **WLAN UART provider:** a custom compatible on what looks like SoC UART2
  (`0x20a0c8000`). Check whether it is a plain `apple,s5l-uart` that the
  Wi-Fi power sequence only needs to keep powered. If so, describe it as
  `serial2` and reference it from the Wi-Fi node.
- **Wi-Fi calibration source:** Paul loads it from NVMEM SysCfg (pauli1go
  0036); we use the m1n1 `brcm,cal-blob`. Pick one mechanism for all devices.
- **Touch firmware names:** J172 uses `apple/dfrmtfw-j172-k1f19-6.bin` and we
  use `apple/j71b-fwstart.z2fw`. Use one scheme, mainline's `dfrmtfw-*`
  naming, together with the firmware recipe.


## m1n1 (2026-10-28)

`boot/m1n1/patches/0001` replaces the J71b-only loader patch and checks no
board compatibles. Validated on J71b with the torvalds-base kernel:
- Wi-Fi `local-mac-address` (6 bytes) and `brcm,cal-blob` (1780 bytes) are
  filled from the ADT, and no antenna SKU is set; association, DHCP and SSH
  work;
- the NVMe HMB `reg` is filled from `nvme-mmu0 sart-region`;
- Bluetooth uses the unit's address (`local-bd-address` via `bluetooth0`).
  btbcm flags the BCM4355C1 default address as invalid, so hci_bcm takes the
  DT address. The "Using default device address" log line is printed before
  that.
The calibration mechanism for all T8010 boards is therefore the ADT (no host
SysCfg payload), which settles that backlog item for J71b. Whether the J172
and iPhone 7 ADTs carry `wifi-calibration-msf` is unverified.

# iPhone X (d221) A11 PCIe topology

The A11 (`t8015`) has a PCIe host bridge and Wi-Fi that no current device in
this repository describes. This file records the controller and DART topology
read from a physical iPhone X's runtime ADT (see `d221-bringup.md`), so the
driver and device-tree work has a single source. It mirrors what the T8010
PCIe bring-up did, but the A11 layout differs from it in several places.

Everything below is from the ADT of one `iPhone10,6` (`d221`), iBoot
`3332.0.0.1.23`.

## Host bridge (`apcie`)

| Field | Value |
| --- | --- |
| `compatible` | `apcie,t8015` |
| config | `0x610000000` (0x1000000) |
| `rc` | `0x600000000` (0x50000) |
| `phy` | `0x60008000` (0x4000) |
| `phy-ip` | `0x6000c0000` (0x40000) |
| port regs | `0x60N000000` + nvmmu at `0x60N004000`, N = 1..4, each 0x4000 |
| interrupts (SPI) | 340, 343, 346, 349 (ports 0–3) |
| `clock-gates` | 56, 57, 58 |
| `power-gates` | 56 |
| MSI | address `0xc0000000`, vector offset 352 |
| `perst-to-refclk-delay-us` | 60 |

The port register/files span in the ADT is `0x60N000000`/`0x60N004000` per
port, so there is a per-port NVMMU at `0x60N004000` that the T8010 layout
folds into a single `nvmmu` range.

The ADT also carries the SoC tunables this controller needs:
`apcie-common-tunables`, `apcie-phy-tunables`, and per-bridge
`apcie-config-tunables` / `pcie-rc-tunables`. These are the A11-specific
PHY/config register programs; they are not transcribed here, and the h9p
driver has no T8015 match-data yet.

## Wi-Fi port (pci-bridge3)

| Field | Value |
| --- | --- |
| `apcie-port` | 3 |
| `maximum-link-speed` | 2 |
| `msi-vector-base` | 24 |
| `function-clkreq` | GPIO 0xa3 |
| `function-device_wake` | GPIO 0x28 |
| `function-perst` | GPIO 0xa7 |
| endpoint | `wlan-pcie,bcm4357` (the `herculesk` module, see the d221 recipe), iommu-parent = the `dart-apcie3` mapper |

## DARTs

Both live under the controller, `compatible = dart,t8015`, page-size 4096,
`vm-offset 0x8000000000`, `vm-size 0x40001000`, `dart-options 7`,
`error-reflector 0x233ffc000`, `stt-idx-width 10`, `fetch-config 0xe0303`,
`diag-config 0x80000400`.

| DART | reg | interrupt (SPI) | mapper |
| --- | --- | --- | --- |
| `dart-apcie2` | `0x603008000` | 347 | `mapper-apcie2` |
| `dart-apcie3` | `0x604008000` | 350 | `mapper-apcie3` (the Wi-Fi's iommu-parent) |

## What remains for a working Wi-Fi port

1. h9p PCIe driver: a T8015 match-data entry, including the A11 tunables and
   the per-port NVMMU layout.
2. `t8015.dtsi`: the `apcie` controller and `dart-apcie2/3` nodes, with the
   clock-gate (56/57/58) and power-gate (56) indices mapped to `pmgr` domains.
3. Board wiring (`pci@3,0` + `wlan` node) for `t8015-x` with the pinmux from
   `function-clkreq/device_wake/perst` above, mirroring the T8010 board patch.
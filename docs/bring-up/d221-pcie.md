# iPhone X (d221) A11 PCIe topology

The A11 (`t8015`) has a PCIe host bridge and Wi-Fi that no current device in
this repository describes. This file records the controller and DART topology
read from a physical iPhone X's runtime ADT (see `d221-bringup.md`), so the
driver and device-tree work has a single source. It mirrors what the T8010
PCIe bring-up did, but the A11 layout differs from it in several places.

The values below are from the runtime ADT of one `iPhone10,6` (`d221`),
firmware `iBoot-8422.142.2.700.1`. The property and node names used in this
file (`rc`, `phy`, `phy-ip`, `nvmmu`, `portN`) follow the driver and this
repository; they are not ADT properties, which are unnamed.

## Host bridge (`apcie`)

| Field | Value |
| --- | --- |
| `compatible` | `apcie,t8015` in the ADT; `apple,t8015-pcie` in the device tree |
| config | `0x610000000` (0x1000000) |
| `rc` | `0x600000000` (0x50000) |
| `phy` | `0x600080000` (0x4000) |
| `phy-ip` | `0x6000c0000` (0x40000) |
| port regs | `0x60N000000` + nvmmu at `0x60N004000`, N = 1..4, each 0x4000 |
| interrupts (SPI) | 340, 343, 346, 349 (ports 0–3) |
| `clock-gates` | 56, 57, 58 |
| `power-gates` | 56 |
| MSI | address `0xbffff000`, vector offset 352 |
| `perst-to-refclk-delay-us` | 60 |

The ADT lists a 0x4000 window at `0x60N004000` next to each port's register
block at `0x60N000000`. This series reads that window as a per-port NVMMU;
that is an inference, not an ADT property name. The T8010 layout carries a
single `nvmmu` window at port 0 + 0x4000 instead.

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

Bus numbering follows from what the driver does at runtime, not from the ADT:
it hides ports that are not enabled and the host bridge renumbers buses, so
with only port 3 enabled this port comes up as bus 1 and the endpoint's
requester ID is `0x100`. The board `iommu-map` entry uses that ID.

## DARTs

Both are siblings of the controller in the ADT, `compatible =
"apple,t8015-dart", "apple,s5l8960x-dart"`, page size 4096, `dart-options 7`,
`error-reflector 0x233ffc000`, `stt-idx-width 10`, `fetch-config 0xe0303`
and `diag-config 0x80000100` (the `0x80000100` differs from the `0x00000100`
of the s5l8960x, t7001 and s8003 DARTs).

The ADT `vm-offset` is `0x0800000080000000` and `vm-size` is `0x3ffff000`.
The board `iommu-map` uses a `0x80000000` aperture of `0x3ffff000` bytes,
the same pair the other Apple boards use; the meaning of bit 59 in
`vm-offset` is not established here.

| DART | reg | interrupt (SPI) | mapper |
| --- | --- | --- | --- |
| `dart-apcie2` | `0x603008000` | 347 | `mapper-apcie2` |
| `dart-apcie3` | `0x604008000` | 350 | `mapper-apcie3` (the Wi-Fi's iommu-parent) |

## What remains for a working Wi-Fi port

1. h9p PCIe driver: a T8015 match-data entry with the A11 tunables, the
   per-port NVMMU layout and the link-training sequence.
2. The device-tree series that describes the controller, its DARTs and the
   Wi-Fi port is written but held; it carries every new node as `disabled`
   until the driver match data exists.
3. Power control: the module's regulator and reset sequence, from the ADT
   `function-reg_on` (SMC key `gP0e`) and the pwrctrl sequence the BCM4350
   path already uses.
4. Host wake: the endpoint's host-wake interrupt, alongside the
   `device-wake` line in the table above.
5. brcmfmac has no BCM4357 entry, so the Wi-Fi driver side is missing too.
6. The endpoint's PCI ID is not known; `0x43d9` is the BCM43570's ID, so the
   `wifi@0,0` node stays out of the device tree until the ID is read from
   the hardware.

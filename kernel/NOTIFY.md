# What to tell contributors

The running list of what other projects need to hear from us: fixes to
offer, deviations from their trees, and findings that affect their devices.
Add each item when the change is made, and remove it once it has been sent
and answered. Patch paths are relative to `kernel/`.

## Nick Chan (Hoolock)

- DART S5L8960X fault handler NULL dereference. Fix:
  `patches/drivers/iommu/0001`.
- The S5L8960X fault message prints the IOVA after the window mask, so a
  fault at 0xbffff170 reads as 0x3ffff170. The older driver printed
  `addr | dma_min`. This is only an observation; it misled our Wi-Fi
  debugging.

## Paul Praschl (pauli1go: J171/J172, D10/D11/D101/D111)

- **Affects all his T8010 devices on the current Hoolock base:**
  `t8010.dtsi` gives the PCIe DART windows with `apple,dma-range`. Hoolock
  6831bc70's DART driver no longer reads that property; it expects the
  window in the five-cell specifier. Without it Wi-Fi faults on its first
  DMA (seen on J71b), and the NVMe DART loses its window too. Fix:
  `patches/devices/t8010/0002`.
- H9P `apple,manual-availability-iommu` was parsed with `#iommu-cells` and
  broke on five-cell DARTs. It now parses one fixed stream cell, and the
  binding describes the cells. Fix: `patches/drivers/pci/0001`.
- ANS: release the FlatDMA slot before unmap (`patches/drivers/nvme/0001`).
- SmartIO: take RX interrupts only while the coprocessor is up
  (`patches/drivers/soc-apple/0001`).
- H9P: the System-RAM overlap check.
- bq27540 entry (`patches/drivers/power/0001`), and the question of whether
  values are served from cache only.
- apple_z2: J71b is a new variant on his J172 paths. Like J172, it signals
  firmware-ready twice. (`patches/drivers/input/0001`, `0002`)
- The iPad DT layout now follows mainline. `t8010-ipad6.dtsi` describes the
  iPad 6 board and `t8010-ipad7.dtsi` overrides it. The WLAN UART
  platform-control block moved into `t8010.dtsi` as a disabled SoC node.
  J171/J172 DTBs are unchanged apart from label names and the new
  `wifi0`/`bluetooth0` aliases. (`patches/devices/t8010/0001`)
- We are reworking his layer toward mainline practice (see REVIEW.md,
  "Mainline-practice fixes for the imported pauli1go layer"). This is for
  information; we maintain it, nothing is expected from him.
- brcmfmac RX buffers are now mapped bidirectionally for everyone
  (`patches/drivers/wifi/0003`): on J71b the BCM4355C1 firmware reads them
  during association. It is likely the same on J172 (same chip).
- Planned changes to his code, once done: the board-named
  `apple,j172-pcie` compatible, the `"XX"` antenna SKU, one Wi-Fi
  calibration mechanism, and the apple_z2 DT tuning properties.

## Berkay Kösebay (J71t: hardware PR #6, PR #8, cli PR #2)

- Nothing to send yet. The PRs are under our own review and are not adopted
  as they are (see REVIEW.md, "Unverified sources").

## linux-wireless (brcmfmac)

- Don't cancel reset work that was never initialized
  (`patches/drivers/wifi/0001`).
- Select Apple NVRAM by OTP when there is no antenna SKU
  (`patches/drivers/wifi/0002`).
- msgbuf: map RX buffers for device reads too (`patches/drivers/wifi/0003`).

## linux-bluetooth (hci_bcm)

- iPad 6 BCM4355C1 data and quirks (`patches/drivers/bluetooth/0001`).

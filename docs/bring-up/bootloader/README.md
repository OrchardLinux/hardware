# Bootloader bring-up

Orchard's boot chain is tethered on every device: checkm8 → Pongo → m1n1 →
Linux, started from a host. Today the host appends one kernel, DTB and
initramfs to m1n1. The goal is a bootloader that finds the installed systems
on the device, lets the host pick one, and boots it with that system's own
kernel. Open questions are written as runnable experiments in
[experiments.md](experiments.md).

## Direction (2026-10-29)

**m1n1 is the bootloader**, as in Asahi Linux. It reads the device's storage,
loads the selected entry's kernel, initramfs and DTB, applies the ADT fill-ins
to that DTB and boots it. This is a fresh boot, not a hand-over from a running kernel.

- Storage access in the loader is the Asahi pattern: upstream m1n1 reads its
  stage 2 from the EFI system partition through its own NVMe driver, GPT parser and FAT reader.
- Entries are [Boot Loader Specification](https://uapi-group.org/specifications/specs/boot_loader_specification/)
  Type #1 files on a FAT boot partition. Distro tooling such as `kernel-install` already writes them.
- At first the host drives selection over m1n1's USB proxy: m1n1 reads blocks,
  the host parses the partition table, the filesystem and the entries, and m1n1 loads and boots.
  An on-device menu, or U-Boot/EFI on top of the same storage driver, can come later.

Rejected for now: a Linux discovery bootstrap that kexecs the selected
kernel (`omarchy-next`, never run on hardware). Every driver the bootstrap
uses must hand its device over warm. On A9, Berkay Kösebay's kexec work shows
the storage link failing to retrain after a hand-over (`POLL_COMPLIANCE`) in some
boots. A10 and A11 have no EL3, so his PSCI-based CPU hand-over cannot apply there.

## What m1n1 already has

`boot/m1n1` builds Asahi m1n1 with Hoolock (Nick Chan) and Orchard patch layers.

| Piece | State |
|---|---|
| GPT, FAT, APFS readers (Rust), `chainload` | Asahi upstream |
| Generic `main_storage_init/read/shutdown` | Hoolock |
| A7/A8/A8X storage (ANS1) | Hoolock |
| A11 storage (ANS2 NVMe, `nvme.c` T8015) | Hoolock |
| **A9/A9X/A10/A10X storage (NVMe behind the H9P PCIe bridge)** | **Missing**: `main_storage_init` prints "storage not supported" |
| Storage over the proxy (`nvme_read`, `ans1_read`) | Hoolock, for the supported chips |
| Linux boot over the proxy (`proxyclient/tools/linux.py`) | Asahi upstream; first J71b run is [BL-00](experiments.md#bl-00) |

Small upstream fix: the C `chainload_load()` still calls `nvme_init()` directly,
while the Rust side uses the generic storage functions.

## T8010 storage path (from the Linux drivers)

Sources: `pcie-apple-h9p.c` (Paul Praschl, T8010; S8003 match data from Berkay
Kösebay), `pci-apple-ans.c`, `t8010.dtsi`. Only the parts a read-only loader needs:

1. **Power and clocks:** the `ps_pcie` power domain, plus the `ps_pcie_aux` and `ps_pcie_ref` gates.
2. **Bridge common setup:** RC `CTL0=INIT`, port-0 `CTL1` enable, then wait for
   `COMMON_STAT` `INIT_DONE` and `READY`. Write `CTL3`, apply the common tunables, write `CTL1`, wait 5 ms, write `CTL2`.
3. **Port 0:**
   1. Assert PERST# (AP GPIO 12).
   2. Enable the RC port and wait for the PHY lane mask.
   3. Program the PHY lanes and wait for CLKREQ# (GPIO 16).
   4. Set the target link speed to 8 GT/s, then apply the config and port tunables.
   5. Set the port enable bit, `PWRCTL=0x31` and the MSI base.
   6. Deassert PERST#, enable LTSSM, and poll `LINKSTS` until L0 (`0x11`), with a 500 ms limit.
4. **NVMMU:** program the TCB base and table, then the SART window that maps the
   host memory buffer: IOVA `0xbc000000` → the `nvme-mmu0` `sart-region`
   carve-out from the ADT, which m1n1 already reads for `nvme-hmb`.
5. **ANS endpoint:** set up the vendor registers.
   - Read `0x1808`/`0x180c` (the HMB size and alignment the controller requests).
   - Clear CC and wait for RDY to drop.
   - Retire all 36 TCBs.
   - Write `INIT=0` and the HMB base and size.
   - Then run the normal NVMe enable.
6. **Data:** each read command uses one TCB.
   - Fill in the page list and mark the TCB read-direction. The page count is written last, because a non-zero count is what makes the TCB valid.
   - Set command flag bit 5 (FlatDMA) and `PRP1 = 0x40000000 + tag * 8 MiB`.
   - Poll the completion queue. A loader needs no MSI.

Open in that list:
- **Queue memory DMA.** The S5L8960X DART has no bypass setting
  (`tcr_bypass = 0` in Linux), and neither m1n1 nor its proxyclient supports
  that DART type. Submission and completion queues need an IOVA through
  `pcie0_dart0` (requester ID `0x100`). That takes a minimal page table in its format:
  tables `pa | 3`, leaves `pfn << 12 | 2`. See the J71t ledger, item 1.3.
- **Which tunables are SoC constants.** Berkay found the S8003 values identical across bridges.

## The hand-over risk

On our checkm8 path, nothing before Linux has ever touched ANS, the NVMMU,
the DART or the PCIe ports: Pongo has no PCIe code, and iBoot does not run.
Linux has always started the controller from **power-on reset**, and that is
the only state it is proven to start from. A storage-reading m1n1 changes
that, the same class of problem as Berkay's kexec work.

Berkay's analysis of the J71t iBoot (`docs/boot/ds-nvme-d-iboot-handoff-state.md`
in his tree) gives Apple's own precedents:
- **iBoot's "stop ANS" sequence:** `SHN=01` → wait for `CSTS & 0xc == 8` → `CC.EN=0` → wait for `RDY=0`.
- **iBoot pairs the controller state with the port state:** controller down,
  then port down by clearing RC `0x124`, `0x114` and `0x100`.

m1n1's shutdown should copy that pairing, then assert PERST# and power the
`ps_pcie` domain off. Whether Linux then comes up as cleanly as from power-on reset
is [BL-03](experiments.md#bl-03). It is the experiment that decides this design.

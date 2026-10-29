# Bootloader experiments

Each entry is one precise experiment that closes a gap in
[the bootloader guide](README.md). Entries say which facts come from our sources
and mark our own reasoning as *inferred*.

## Rules for every experiment

1. **Read-only storage.** No experiment writes to NVMe. Write and flush commands
   are never built, not even behind a flag.
2. **One attempt per boot.** Never retry a boot, link bring-up or controller
   enable automatically. A failure is recorded and the device is restarted.
3. **Change one thing.** Compare against a known-good boot that differs only in
   the step under test.

## BL-00

**m1n1 USB proxy and a host-chosen kernel on J71b.**

Boot a bare m1n1 (no payload) through Pongo. Then load the `0.1.11-local.3`
recovery's kernel, DTB, initramfs and boot arguments with
`proxyclient/tools/linux.py`. Only the delivery differs from `recovery.bin`.
The prepared kit is `out/bootloader-e0/` (outside git; `run` and `README.md`).

Pass:
- the m1n1 proxy serial port appears;
- `linux.py` reaches "Ready to boot";
- "J71b Recovery" enumerates and SSH works;
- the Wi-Fi MAC and the touch calibration in `/proc/device-tree` match a normal RAM boot.

Send back: `linux.py.log`, and whether m1n1's console stayed readable on screen.

## BL-01

**T8010 PCIe port 0 link from m1n1, read-only, over the proxy.** Prototype in Python
(`proxyclient/experiments/`), the way Hoolock prototyped ANS1. Steps 1–3 of
[the T8010 storage path](README.md#t8010-storage-path-from-the-linux-drivers),
then stop.

Pass: port 0 reaches L0, and the endpoint's config space reads vendor `0x106b`.

Send back: the `LINKSTS` and LTSSM values at each step, plus the endpoint vendor, device and class.

## BL-02

**Read the GPT header from m1n1.** Continue BL-01 with the NVMMU, a minimal
`pcie0_dart0` table for the queues, the ANS registers, controller enable, Identify, and a
FlatDMA read of LBA 0–1. Then shut down per BL-03.

Pass: LBA 1 starts with `EFI PART`, and the disk GUID matches `sgdisk -p` from a Linux boot.

Send back: the HMB size and alignment requested, the Identify model and firmware strings, and the GPT disk GUID.

## BL-03

**Linux storage after m1n1 has used it.** This experiment decides the design.
Run BL-02 including the shutdown, then boot the BL-00 kernel over the same proxy session.
The shutdown sequence:
1. iBoot's ANS stop.
2. Port down (RC `0x124`, `0x114`, `0x100`).
3. PERST# asserted.
4. `ps_pcie` off.

Pass: Linux mounts NVMe and survives a 10-minute read soak (`dd` of the root
partition to `/dev/null`) without timeouts. Repeat over 5 cold boots.

If it fails, record the LTSSM state Linux reports. Compare with Berkay's
`POLL_COMPLIANCE` result after kexec. Then try once with `ps_pcie` left on,
to see whether powering the domain off helps or hurts.

## BL-04

**m1n1 C storage backend for A9/A10.** Port BL-02 into `main_storage_*` as an
`s8000`/`t8010` backend. Then load a BLS entry from a FAT boot partition
(host-parsed at first).

Pass: BL-00's kernel boots from the boot partition instead of the host.

## Per-device menu

| Device | SoC | Storage in m1n1 | Run |
|---|---|---|---|
| iPad 6 (J71b), iPad 7 (J171/J172), iPhone 7 (D10/D11, D101/D111) | T8010 | Missing | BL-00 → BL-04 |
| iPad 5 (J71t and others) | S8003/S8000 | Missing | BL-00, then BL-01 with S8003 match data |
| iPad Air 2 (J81/J82) | T7001 | Exists (ANS1; *inferred*: m1n1 names only A9/A9X/A10/A10X as unsupported) | BL-00, then `main_storage` read of LBA 1 directly |
| A11 devices | T8015 | Exists | BL-00, then `main_storage` read of LBA 1 directly |

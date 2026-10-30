# iPhone X (GSM) bring-up

The iPhone X GSM, `iPhone10,6` / A1901, is board `d221` on an Apple A11 T8015.
Nothing in this repository has booted that SoC before. This file is the first
physical record: the DFU identity, the boot chain through m1n1, a Linux RAM
boot, and the gaps, all observed on one dedicated test unit.

## Test environment (2026-10-30)

| Piece | Value |
| --- | --- |
| Host | Arch Linux ARM, aarch64, Apple Silicon |
| Orchard | CLI `a2dd45f`, hardware `0cfb171` (`./orchard`, `orchard 0.1.11`) |
| palera1n | 2.4 |
| Kernel | `7.3.0-rc1-orchard-apple-16k`, DTB `t8015-d221`, 69 modules |
| m1n1 | built by `./boot/m1n1/build d221`; payload `39d580bc39af8fa9b8c2830c95f6b9bb82ad0f02f9837a90bc3f7a4ea9a36295` |
| Pongo | palera1n 2.4's bundled image |
| RAM payload | `d204dfdda2f3a7adcc7525f44c341580427fd9345d438078ff07314695a9802d` |

## DFU identity

From one unit's DFU serial string:

```
CPID:8015 CPRV:11 CPFM:03 SCEP:01 BDID:0E ECID:00022CA93C2B002E IBFL:3C SRTG:[iBoot-3332.0.0.1.23]
```

| Field | Value |
| --- | --- |
| `CPID` (chip) | `0x8015` — A11 T8015 |
| `BDID` (board) | `0x0e` |
| `ECID` | `0x22ca93c2b002e` |
| `SRTG` (iBoot) | `iBoot-3332.0.0.1.23` |

`[match.dfu]` for this board is `chip = 0x8015`, `board = 0x0e`. The board ID
alone does not identify it: `0x0e` is also the `d111` (A10) board, so the pair
must match together, as the schema already does.

## Boot chain (observed on this unit)

```
checkm8 → Pongo → m1n1 → Linux 7.3.0
```

Every stage was seen on the physical device:

- **checkm8:** palera1n reports `Checkmate!` on `8015 11`.
- **Pongo:** enumerates as `05ac:4141` `PongoOS USB Device`.
- **m1n1:** enumerates as `1209:316d` uartproxy. It reads the ADT (242 368 bytes,
  sha256 `9a9ce7be…`) and initialises the internal display at `1125x2436`.
- **Linux:** the combined RAM payload boots and exposes a USB serial shell.

### The one operational trap

The `download mode` step after `Checkmate!` is unreliable on this host:

- Entering DFU straight from normal mode, then checkm8, often ends in
  `Timed out waiting for download mode` on a direct USB-C cable and through
  some hubs.
- It is the documented A11 + USB-C/Apple-Silicon problem. palera1n succeeds
  through a USB-A port of a USB 2.0 hub, with `apple_mfi_fastcharge` reloaded
  and `usbmuxd` stopped; sometimes a device replug after `Checkmate!` is needed.

## Linux RAM boot

```console
$ uname -a
Linux (none) 7.3.0-rc1-orchard-apple-16k #1 SMP PREEMPT aarch64 GNU/Linux
$ nproc
6
$ free -m
Mem: 2780 total
```

- CPUs online: `0-5` (6 of the A11's cores)
- Framebuffer: m1n1 initialises the panel at `1125x2436`; `fb0` reports
  `virtual_size 1125x2346`
- USB: `dwc2` device mode, `configfs-gadget.minimal-boot` provides the serial shell
- RTC: `rtc-apple-pmic` registers and sets the clock
- Reboot/poweroff: handled through `macsmc-reboot`

What the kernel reports as not yet present (gaps for the recipe and port):

- `pmp-t8015-v1`: `Direct firmware load for apple/pmp-t8015.bin failed` — the
  power-management coprocessor firmware is not in the recipe yet.
- `apple-pmgr-pwrstate`: several `sync_state() pending due to 232300000.pmp`
  and `259cc0000.nvme` — the NVMe storage controller is detected but not used
  (the boot is RAM-only; internal storage stays inactive).

## Firmware

The device tree from the newest full restore Apple signs for this board
(`iPhone10,6`, iOS 16.7.16 / 20H392, sha256
`ece1194f…`) gives the board facts without touching the device:

| Subsystem | Finding |
| --- | --- |
| Wi-Fi | `wlan-pcie,bcm4357`, `module-instance herculesk`, firmware dir `C-4357__s-B1` |
| Bluetooth | `bluetooth,n88` |
| Touch | `hid-transport,k1`, image-tag `wftm`, `hid-merge-personality C1R1FC0,1` — not the Z2 the iPad/iPhone 7 use |

`devices/d221/firmware.json` ships only the Wi-Fi set for now (BCM4357
`herculesk` trx/clmb/txcb and the two real GINS NVRAM profiles), each pinned by
sha256. Bluetooth HCD, touch and PMP firmware are still to be extracted.

## What is untested

Display (full DRM/GPU), touch, Wi-Fi, Bluetooth, mounting the NVMe root,
battery/charging, backlight, audio, camera and cellular. The A11-specific work
still needed before this board is usable beyond a RAM shell:

1. `apple/pmp-t8015.bin` (and any other coprocessor firmware) in the recipe.
2. A touch driver for the `hid-transport,k1` controller — the Z2 drivers here
   do not cover it.
3. The `t8015-x`/`d221` device tree nodes for Wi-Fi, Bluetooth and backlight
   (upstream describes the SoC alone).
4. An installer and launcher release, then `resize-apfs` qualification on this
   board.
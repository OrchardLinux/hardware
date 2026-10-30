# iPhone X (GSM) DFU identity

The iPhone X GSM, `iPhone10,6` / A1901, is board `d221` on an Apple A11 T8015.
It has no descriptor here yet, so this file records the first physical
observation and nothing more: what a DFU session reports, and what is still
unknown. No payload has been booted on this board.

Identity from one DFU session on 2026-10-29:

| Field | Value |
| --- | --- |
| USB ID | `05ac:1227` (Apple DFU) |
| `CPID` | `0x8015` (A11 T8015) |
| `BDID` | `0x0e` |
| `ECID` | `0x22ca93c2b002e` |
| `CPRV` / `CPFM` / `SCEP` | `0x11` / `0x03` / `0x01` |
| `SRTG` | `iBoot-3332.0.0.1.23` |
| `IBFL` | `0x3c` |

The DFU serial string, as read from the device:

```
CPID:8015 CPRV:11 CPFM:03 SCEP:01 BDID:0E ECID:00022CA93C2B002E IBFL:3C SRTG:[iBoot-3332.0.0.1.23]
```

`orchard devices` reported the same session as `mode: "dfu"`, `chip: 32789`,
`board: 14`, `ecid: 612055323967534`, `product: "Apple Mobile Device (DFU Mode)"`
and `pwned: false`.

## What this supports

- The `[match.dfu]` pair for this board is `chip = 0x8015`, `board = 0x0e`. A
  board ID alone does not identify a device: `0x0e` is also the `d111` (A10)
  board, so the chip and board must be matched together, as the descriptor
  schema already does.
- DFU enumeration works from a Linux ARM64 host through two USB 2.0 hubs, with
  no vendor driver involved. The host ran Arch Linux ARM (aarch64) with Orchard
  `206f69c`, hardware `ae1457e` and palera1n 2.4, and `orchard devices` read the
  identity without `usbmuxd` present.
- The upstream device tree for this board already exists in
  `torvalds/linux` as `arch/arm64/boot/dts/apple/t8015-d221.dts` ("Apple iPhone X
  (GSM), D221, iPhone10,6 (A1901)"), including `t8015-x.dtsi`. The `hoolock`
  branch carries the T8015 trees for `d20` and `d201` only, so a `d221` port
  needs that tree carried as a device patch, with its original author.

## What is still unknown

- Whether Pongo and m1n1 start on an A11 at all. Nothing in this repository has
  booted that SoC, and no Orchard release has a T8015 `d221` launcher.
- The firmware recipe: Wi-Fi, Bluetooth HCD, multitouch and SmartIO blobs for
  `iPhone10,6` have not been extracted or identified. Until they are, no
  `devices/d221/` descriptor can be added, because the schema requires a recipe
  with `ipsw`, `inputs` and `outputs`.
- Touch, display, storage and charging. This board's display panel and touch
  controller are not described anywhere in this repository.

## Next steps, in order

1. Boot a RAM payload on a `d221` and record what happens: whether the Pongo
   session starts, whether m1n1 reports the board, and the page size and
   framebuffer if a console appears.
2. Extract the `iPhone10,6` firmware inputs from a full restore of the newest
   iOS 16 build Apple still signs for this board, and write
   `devices/d221/firmware.json` with checksums, as the other devices do.
3. Add `devices/d221/device.toml` once the recipe exists, with every feature
   `untested` or `missing` until it is seen working on this board.

# Touch calibration on Apple Z2 touchscreens

How per-unit touch calibration reaches the touch controller, and how to
check a new device. Verified on J71b (iPad 6) on 2026-10-28.

## The short version

The touch firmware file (`*.z2fw`) is the same for every unit of a model.
It carries *calibration records* instead of calibration data. At boot:

1. iBoot passes the ADT, which holds this unit's calibration in the
   touchscreen's ADT node.
2. m1n1 copies the calibration properties into the DT node that the
   `touchscreen0` alias points to (`boot/m1n1/patches/0001`).
3. When it uploads the firmware, `apple_z2` reads each calibration
   record (address, maximum size, provider), takes the matching DT
   property, and sends it as an HBPP blob packet
   (`patches/drivers/input/0001`).

No host-side ADT extraction is needed for normal installs. The firmware
itself is still extracted per model (Apple's firmware is not
redistributable).

## J71b reference values

ADT node: `/arm-io/spi3/multi-touch` (m1n1 finds it below its SPI host).

| ADT property | DT property | J71b size |
|---|---|---|
| `multi-touch-calibration` | `apple,z2-cal-blob` | 1280 |
| `multitouch-to-display-offset` | `apple,z2-display-offset-cal-blob` | 128 |
| `firefly-calibration` | `apple,z2-firefly-cal-blob` | 5077 |
| `pressure-effect-correction` | `apple,z2-pressure-cal-blob` | 29700 |

The iPhone 7 Plus uses `multi-touch-calibration` plus `orb-gap-cal`,
`orb-force-cal` and `shape-dynamic-accel-cal`.

The touch profile (`J71b.mtprops`, profile `K1F19,4`) lists the
calibration sequence: provider name, load address and maximum size per
blob. The converter turns each entry into a calibration record.

## Packet format

A calibration packet is an HBPP blob command:

```
18 e1 | 30 01 | len (BE16) | addr low (BE16) | addr high (BE16) |
header checksum (BE16, sum of the 6 len/addr bytes) |
data, zero-padded to 4 bytes, 16-bit byte-swapped |
data checksum (sum of unpadded bytes, low BE16 then high BE16)
```

`len` differs by HBPP version:
- HBPP 0x103 (iPhone 7 Plus): number of 4-byte words.
- HBPP 0x104 (J71b): number of 4-byte words minus one.

The ADT's `hbpp-version` property tells which one a device uses.

## Checking a new device

1. Dump the ADT during bring-up. Find the node with
   `multi-touch-calibration`, and list its calibration-looking properties
   with sizes. Values starting with `syscfg/` are unprovisioned
   references, not data.
2. Read the device's mtprops calibration sequence. Every provider there
   needs an ADT property, a DT property name (m1n1 mapping and binding)
   and an `apple_z2` provider ID.
3. Note `hbpp-version` and pick the matching length encoding.
4. Generate the uncalibrated firmware. Offline, compare each calibration
   packet the driver would build from the ADT data with the packets a
   calibrated build bakes in (the J71b check matched 4 of 4 byte for
   byte).
5. On the device: confirm the four DT properties exist, the firmware
   loads, and touch reports events over the full panel.

## History

Until 2026-10-28 the CLI baked the unit's calibration into the firmware
file (`cli/src/orchard_cli/j71b/touch.py`, `convert()` with ADT
properties). That needed the device's ADT on the host and made the file
private to one unit. Find that implementation in the CLI's git history
before the commit that replaced it with calibration records.

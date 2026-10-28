# Firmware recipes

Apple's firmware can't be redistributed, so each user's Orchard tooling
extracts it locally. A device's `firmware.json` (schema 2,
`schemas/v1/firmware.schema.json`) describes where every input comes from
and which declarative steps turn it into an installed file. It contains no
code: the steps come from a fixed, versioned vocabulary that the Orchard CLI
implements.

## Structure

- `device`: the Apple product (`iPad7,5`).
- `ipsw`: the pinned restore image (version, build, URL, size, sha256).
- `inputs`: named sources. Each lists where to find it:
  - `ipsw`: `{"rootfs": path}`, `{"zip": member}` or `{"kernelcache": true}`;
  - `device` (optional): the same file's path on a running iPadOS, or
    `{"kernelcache": "active"}`.
  An optional `sha256` pins the input.
- `outputs`: installed files. Each has a `destination` under
  `lib/firmware/`, a pinned `sha256`, and `steps`.

The host reads `ipsw` sources. The in-app installer reads `device` sources
when a recipe lists them, and falls back to the IPSW when an input is
missing there or its hash differs. Every output must match its pinned hash
either way. Per-unit data (MAC addresses, Wi-Fi, Bluetooth and touch
calibration) never goes into firmware files: m1n1 copies it from the ADT
at boot.

## Steps (version 1)

| Step | Parameters | Produces |
|---|---|---|
| `copy@1` | `input` | the input unchanged |
| `bluetool-hcd@1` | `input` (BlueTool) | the Bluetooth patchram whose hash is the output's |
| `smartio-from-kernelcache@1` | `input` (kernelcache) | SmartIO coprocessor firmware |
| `im4p-payload@1` | `input` | the payload of an IM4P container |
| `z2fw-from-mtprops@1` | `input`, `profile`, `packet`, `load_address` | Z2 touch firmware with calibration records |

Before it extracts anything, the CLI checks that it implements every step a
recipe uses. If not, it asks for an Orchard update. New devices that reuse
known steps need only a hardware update.

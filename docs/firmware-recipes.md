# Firmware recipes

Apple's firmware can't be redistributed, so each user's Orchard tooling
extracts it locally. A device's `firmware.json` (schema 2,
`schemas/v1/firmware.schema.json`) describes where every input comes from
and which declarative steps turn it into an installed file. It contains no
code: the steps come from a fixed, versioned vocabulary that the Orchard CLI
implements.

An experimental RAM-boot profile can omit `[firmware]` when its descriptor
explicitly sets `support.install = false` and `support.status = "experimental"`.
Its public RAM components build without firmware acquisition. A request to
prepare firmware for that profile fails before extraction; adding peripheral
support requires a real recipe with pinned inputs and outputs. Profiles that
permit installation continue to require a firmware declaration.

## Structure

- `device`: the Apple product (`iPad7,5`).
- `ipsw`: the pinned restore image (version, build, URL, size, sha256).
- `inputs`: named sources. Each lists where to find it:
  - `ipsw`: `{"rootfs": path}`, `{"zip": member}` or `{"kernelcache": true}`;
  - `device` (optional): the same file's path on a running iPadOS, or
    `{"kernelcache": "active"}`.
  An optional `sha256` pins the input.
- `outputs`: installed files. Each has a `destination` under
  `lib/firmware/`, a pinned `sha256`, and `steps`. The first step names an
  `input`; each later step takes the previous step's result instead.

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
| `z2fw-from-mtprops@1` | `input`, `profile`, `packet`, `load_address`, `hbpp_version` | Z2 touch firmware with calibration records |
| `z2fw-from-mtprops@2` | `input`, `profile`, `packet` | Z2 touch firmware from an mtprops packet array (iPhone 7 Plus) |
| `z2fw-from-mtprops@3` | `input`, `profile`, `packet`, `touch_maximum`, `prox_maximum`, `fll_mval`, `ref_clk_div`, `clk32_clock_enable` | Legacy Gen1 Z2 touch firmware (iPhone 6), with full word counts, bounded calibration from the ADT and the board's kernel clock policy |
| `z2fw-from-mtfw@1` | `profile`, `otp_preflight` | Z2 touch firmware from an MTFW HBPP item list (iPad 7); each calibration loads Apple's default, then its record |

Before it extracts anything, the CLI checks that it implements every step a
recipe uses. If not, it asks for an Orchard update. New devices that reuse
known steps need only a hardware update.

## Legacy Gen1 clock policy

N61's iOS 12.5.1 (16H22) `N61.mtprops` selects `C1F59,1`. Its Z2 data
packet stores the full payload-word count and has no `Firmware Config`.
Step `z2fw-from-mtprops@3` follows the
[Corellium Gen1 bootstrap](https://github.com/corellium/projectsandcastle/blob/03db9c6ae04141eb940f3b9f56d446f50d57fadf/hx-touchd/mtfw/mtfw.c#L169-L219),
with calibration records at the original SysCfg load positions. Provider 9
reads `prox-calibration`; provider 0 reads `multi-touch-calibration`.
N61 bounds them to the observed 128 and 1024 bytes respectively.

The recipe takes clock values from the same IPSW's
`com.apple.driver.AppleMultitouchSPIN61` personality,
`AppleMultitouchSPI - C1,N61`: `fll-mval=6099`, `ref-clk-div-val=2`,
`clk32-clock-enable-val=1`. Resolve the source OSUnserialize XML references:
the latter fields use `IDREF="48"` and `IDREF="8"`, whose integer definitions
are `0x2` and `0x1`. A flattened plist can lose those referenced values.
The decompressed N61 kernel's SHA-256 is
`0145bc0eda01cd55e8b996d24ffc19c25712714326f893ba5085a5016d30a4ad`.
These fields are explicit recipe parameters, including zero-valued overrides.
Physical qualification of firmware loading and input events remains separate
from converting this shared firmware file.

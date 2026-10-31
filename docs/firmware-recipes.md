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
  `lib/firmware/`, a pinned `sha256`, and `steps`. The first step names an
  `input`; each later step takes the previous step's result instead.

The host reads `ipsw` sources. The in-app installer reads `device` sources
when a recipe lists them, and falls back to the IPSW when an input is
missing there or its hash differs. Every output must match its pinned hash
either way. Per-unit data (MAC addresses, Wi-Fi, Bluetooth and touch
calibration) never goes into firmware files: m1n1 copies it from the ADT
at boot.

## How the CLI reads an IPSW

The CLI never downloads the whole IPSW (about 7 GB). It reads only the byte
ranges it needs from Apple's server, about 20-40 MB per device
(`src/orchard/apple/remote_ipsw.py`, standard library only):

1. The ZIP central directory at the end of the file lists the members.
2. `{"zip": member}` inputs are read and inflated directly.
3. `{"rootfs": path}` inputs come from the largest DMG, which IPSWs store
   uncompressed. Its UDIF trailer maps the disk's chunks (zlib or LZFSE,
   `udif.py`); only the chunks a lookup touches are read. The APFS reader
   (`apfs.py`) walks the system volume's B-trees to the file and undoes
   decmpfs compression (zlib, LZVN, LZFSE).
4. `{"kernelcache": true}` takes the kernelcache that `BuildManifest.plist`
   names for the board's `DeviceClass` (`<profile>ap`), unwraps the IM4P and
   decompresses it: LZFSE, or LZSS (`complzss`) on older releases such as
   iPadOS 16 on A9.

The whole IPSW's sha256 can't be checked this way; its size is checked
against the recipe, and every pinned input and every output against its hash.

## Finding firmware during bring-up

The same reader explores an IPSW by hand, by URL or by the IPSW of a device
whose recipe already exists:

```sh
R="python -m orchard.apple.remote_ipsw"
$R https://updates.cdn-apple.com/…/iPad_…_Restore.ipsw members   # ZIP members
$R j71b ls usr/share/firmware/multitouch                           # a system directory
$R j71b get usr/share/firmware/multitouch/J71b.mtprops J71b.mtprops
$R j71b kernelcache j71bap kernelcache.macho                       # decompressed Mach-O
$R j81 dt j81ap wlan bluetooth multi-touch                         # device-tree nodes
```

`get`, `member` and `kernelcache` print the file's size and sha256 for the
recipe. Where a new device's firmware usually is:

| Firmware | Location |
|---|---|
| Wi-Fi (Broadcom, iOS 16 and later) | `System/Library/DriverExtensions/com.apple.DriverKit-AppleBCMWLAN.dext/Firmware/<chip>/` |
| Wi-Fi (iOS 15 and earlier) | `usr/share/firmware/wifi/<chip>/` |
| Bluetooth | patchram embedded in `usr/sbin/BlueTool` (`bluetool-hcd@1`) |
| Touch | `usr/share/firmware/multitouch/<Board>.mtprops` |
| SmartIO, video decoder | the kernelcache (`smartio-from-kernelcache@1`, `im4p-payload@1`) |

Then:

1. Pick the IPSW. api.ipsw.me gives its URL and size; pin the newest
   release the reader can open. iPadOS 18 and later encrypt the IPSW (AEA),
   so those can't be read.
2. Read the board's device tree with `dt`. It is the ADT without what iBoot
   fills in (serials, MAC addresses, calibration), so board facts come from
   the IPSW and need no device: the Wi-Fi `module-instance` is
   brcmfmac's board type (D10 `kristoff`, J81 `rieslinga`), and the chip is
   in `compatible`. BlueTool's embedded patchrams name their board and
   module too ("BCM4355C0 Kristoff MUR MCC").
3. Find the files with `ls` and save them with `get`.
4. Write the inputs and outputs. Check the whole recipe against Apple's
   server, into an empty cache:

   ```sh
   ORCHARD_HOME=$(mktemp -d) python -c "from orchard import firmware
   from orchard.progress import Clock, Progress
   print(firmware.ensure('<profile>', Clock(), Progress(), hardware='.'))"
   ```

If a step is missing from the CLI, add it there first (Steps below).

When the reader can't open something, it says so: an unknown compression,
an encrypted image. For those, and for disassembling kexts, blacktop's
[`ipsw`](https://github.com/blacktop/ipsw) works on a downloaded IPSW:
`ipsw extract --files --pattern 'REGEX' IPSW`, `ipsw extract --kernel
--device iPad7,5 IPSW` and `ipsw kernel dec KERNELCACHE`. On Linux it
mounts the system image with [apfs-fuse](https://github.com/sgan81/apfs-fuse).
The CLI used to do that; it isn't needed for recipes any more.

## Steps (version 1)

| Step | Parameters | Produces |
|---|---|---|
| `copy@1` | `input` | the input unchanged |
| `bluetool-hcd@1` | `input` (BlueTool) | the Bluetooth patchram whose hash is the output's |
| `smartio-from-kernelcache@1` | `input` (kernelcache) | SmartIO coprocessor firmware |
| `im4p-payload@1` | `input` | the payload of an IM4P container |
| `aop-image@1` | `coprocessor` | one image (`AOP`, `AOP_ext`) of an AOP firmware container, the OSSerializeBinary payload of `Firmware/AOP/aopfw-*.im4p` |
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
[Corellium Gen1 bootstrap](https://github.com/corellium/projectsandcastle/blob/083c2d2b07f94c8114cbe7110e1d205906ab8c54/hx-touchd/mtfw/mtfw.c#L169-L219),
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

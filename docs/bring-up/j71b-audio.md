# J71b audio: MCA/transport preparation

This is an unbooted speaker bring-up candidate for J71b (iPad 6 Wi-Fi).
It is based directly on current hardware main, not on the rotation PR.
The device descriptor deliberately retains its existing audio status.

The A9 implementation imported by `fa072cbc` and tested through PR #28 is
the starting point, not the board wiring. Its author describes the
[iPad 5 playback path](https://github.com/brkosebay/omarchy-ipad5/blob/main/docs/audio/playback-path.md).
J71b's board facts were checked against the cached iPadOS 17.7.11 (21H461)
IPSW device tree and matching kernelcache. No raw tree, disassembly,
firmware, calibration or device identifier is part of this change.

## Speaker path

| Resource | J71b description |
|---|---|
| Serializer | MCA2, version 5, `0x20a0a8000/0x4000`, IRQ 229, `ps_mca2` |
| MCLK control word | `0x20a002008/4`; no external codec MCLK is needed here |
| Switch | `0x20a004000/0x1000`, aud2 to pin2 |
| Speaker DMA | Existing SmartIO owner, TX endpoint `0x2c` only |
| TX shim | transfer `0x21`/`0x22`, timeout 0, FIFO `0x180`, trigger `0xcc` |
| Bitclock | AP NCO channel 2, inside `0x20e044000/0x14000` |
| Amps | MAX98357 bank, shared GPIO 20 SD_MODE, initially low |

Only J71b enables the new SoC nodes. The first stream is deliberately
limited to stereo I2S at 48 kHz, S16_LE or S32_LE; MCA supplies BCLK and
LRCLK. The ADT advertises four physical speaker channels. The logical
codec models the common amplifier enable, not four independently
addressable chips. The exact amp variant, GPIO enable behaviour and
physical channel/data-lane mapping still need device qualification.

AppleAE2MCA dispatches both v4 and v5 to its v4 serializer setup routine.
The new `apple,t8010-mca` match data selects version 5, the J71b TX shim,
provider clocking and the corresponding switch route/frame phase. The
existing `apple,s8000-mca` remains a two-channel codec-clock consumer,
with its original rate/format support, MCLK and TX/RX shim values.

The AP NCO uses the existing fractional-divider implementation. Its
T8010-compatible control data selects bit 12 for enable, bit 31 for the
control mode, input zero and the corresponding accumulator initial state.
Existing NCO compatibles keep their original control behaviour. The
reference rate is copied by m1n1 from the NCO's first clock ID in the boot
ADT's clock-frequency table; it is not assumed to be the 24 MHz crystal.
The J71b `nco-ref` alias requests this handoff. Missing clock information
leaves audio disabled and still permits Linux to boot; the NCO driver
rejects a zero reference without touching the speaker clock registers.

Both shared configs select `SND_SOC_MAX98357A=m` and
`COMMON_CLK_APPLE_NCO=m`. The existing MCA, SmartIO and simple-card
selections are retained. These options are not specific to page size.

## How touch stays intact

- SPI3 and the touchscreen node, pinctrl, GPIOs, clocks and DMA phandle
  remain unchanged. The compiled subtree is compared with current main,
  resolving phandles to their target nodes instead of comparing numbering.
- SmartIO's original `0x1a` TX channel stays first in its T8010 range table;
  the only added channel is speaker TX `0x2c`. No RX, PDM, UART or other
  MCA endpoint is exposed by this change.
- The generic DMA driver, touch's default SPI shim (`fifo=0x80`,
  `trigger=0x40`, `limit=0x80`) and shared firmware are unchanged. MCA
  supplies its audio shim through its own peripheral configuration.
- Request tags, pending requests, notifications, DMA queues and stop state
  are per endpoint/channel. Audio stopping does not terminate SPI3 or
  reset/reload SmartIO. The existing shared owner and PM domains remain.
- Playback remains bounded to 16 periods and a 128 KiB buffer. At most
  144 descriptor slots plus an 8-slot configure record are needed for
  that stream, below the shared pool's 2730 slots; tags remain below the
  per-endpoint limit of 31. This does not replace a concurrent touch test.
- The new clock operations target AP NCO channel 2 and MCA2 only. They
  do not change the SPI clock provider or boot its firmware again.

Host regression checks also require the entire iPad 5 J71t DTB to remain
byte-identical and execute the actual frame, route and NCO helpers against
RAM-backed register stubs. Physical touch responsiveness under playback
load is still a required next-stage test.

## Qualification boundary

Kernel/module/DTB compilation, binding and compiled-DT validation,
checkpatch, config resolution and repository tests are host checks. They
do not establish audible output. The next authorized test must first
verify the NCO reference, PCM/card enumeration and touch, then play a
short -36 dBFS fixture, identify channel mapping, repeat start/stop and
exercise touch throughout sustained playback.

No microphone/PDM driver, AOP clock or firmware RPC, UCM2/WirePlumber
policy, headphone detection, GPU change or persistent installation is
included. Apple firmware continues to come from the existing IPSW recipe.

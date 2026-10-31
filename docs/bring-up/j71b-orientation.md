# J71b AOP orientation

The iPad 6 Wi-Fi (J71b, A10) prototype boots the original iPadOS AOP
firmware, negotiates RTKit v12 over the single-word RTBuddy mailbox,
starts its advertised AFK endpoints and reads orientation from endpoint
0x36. This is an exclusive userspace coprocessor driver, not an IIO sensor
driver or support for AOP audio. Other boards remain disabled.

## Components

- `CONFIG_APPLE_AOP_DMA=m` on apple-16k provides `/dev/apple-aop-dma`, mode
  0600. MMIO resources come from the J71b DT node. The ioctl ABI allocates
  coherent buffers (64 slots, 4 MiB per allocation, 16 MiB total) and starts
  the core. `mmap` accepts fixed selectors and owned DMA allocations only;
  it rejects executable/private mappings. Auto-boot control stays in kernel.
- The owner stops the core before freeing DMA. If stopping fails, it keeps
  the buffers. Starting the core pins the module and consumes the one boot
  attempt: stopping the service requires a fresh system boot before another
  start. Do not enable service restart or bind a second mailbox driver.
- Only the 16 KiB page configuration exposes this option. Resolving
  apple-4k leaves it unavailable; its config needs no new entry.
- The m1n1 patch copies optional ADT `/chosen/unique-chip-id` into Linux
  `/chosen/apple,ecid` as a big-endian u64 at boot. The service requires it;
  no device identity belongs in a checked-in DTB or environment file.
- The daemon, systemd units and labwc/touch controller live in omarchy-a.
  The UAPI header copied there must match the header in the owner patch.

## Firmware installation

The J71b recipe uses the existing cached iPadOS 17.7.11 (21H461) IPSW.
Input `Firmware/AOP/aopfw-ipad7baop.RELEASE.im4p` is pinned by hash;
`im4p-payload@1` installs `lib/firmware/apple/j71b-aop.osbin` through the
same private firmware mixin mechanism as Wi-Fi and touch. It requires no
new CLI extraction step and never redistributes Apple's firmware.

The distro launcher verifies the container and both segment hashes before
splitting its OSSerializeBinary `firmware` array into RAM-only file
descriptors. The daemon prepares fresh PRNG and nonce data, inserts the
boot ECID, relocates the external segment through the p0CE data field and
loads SRAM/coherent DMA. Executable instructions and cache settings stay
original. No extracted image, ADT, register capture or per-unit calibration
is included in this repository.

## Qualification

The preceding out-of-tree prototype was tested on installed J71b Linux:
all four physical orientations, touch correction, AFK startup and DMA
lifetime on service stop. Its original kernel was 7.3.0-rc1-orchard-j71b.
The packaged DT driver, kernel-owned start ioctl and identity handoff are
new integration changes and still need a separately authorized device
boot. Local builds and synthetic tests do not establish that qualification.

The software snapshot rotation animation stuttered or barely animated on
the framebuffer renderer. It stays out of this integration; omarchy-a
defaults to direct output and touch transforms with animation disabled.

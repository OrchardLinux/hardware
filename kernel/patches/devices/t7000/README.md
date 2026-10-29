# N61 touch development

The T7000 patches describe SPI2 at `0x20a088000` with the legacy PIO backend,
a 60 MHz source, AIC interrupt 154 and the existing `ps_spi2` power domain.
Its SPI version 1 configuration starts at `0x4018` for N61 mode 0 and 8-bit
words, then becomes `0x4038` during PIO. T7000 uses its own word-size index
shifted by 15 and skips the T8010 descriptor path. The T8010 configuration
and branch selection keep their existing behavior.
N61 uses GPIO185 for SPI chip select while GPIO2 holds touch reset. Per-unit
touch and proximity calibration pass through the native m1n1 touchscreen
alias. The firmware recipe builds `apple/n61-fwstart.z2fw` from the pinned
N61 `C1F59,1` data-form firmware in iOS 12.5.1 build 16H22.

The native clock driver reuses the PMGR gate/divider operations at T7000
offset `0x1e000`. It divides the 24 MHz source by 732 for the 32768 Hz
target. D2186 supplies the core through register `0x0085` bit `0x02`.
Chestnut register `0x05` bit `0x10` controls the analog supply; its enable
prepares shared boost/Sage bits `0x07`. Disabling touch preserves those
shared display bits and every unrelated bit in each register.

The N61 driver enables analog power before core power and keeps reset
asserted through clock startup. After the 15 ms clock wait, it exchanges
`19c1`, waits 10 ms and sends the 16-byte CHECK preflight before releasing
reset. A bounded synchronous CHECK validates both ROM words before upload;
runtime IRQs stay masked until report setup finishes. The firmware stream
preserves 8-bit wire order and holds chip select
across 16 KiB chunks. Provider 0 supplies required touch calibration;
optional proximity uses appended provider 9. Runtime long reads retain the
N61 phase-0 transmit checksum in the phase-1 E6/E7 request.
EB includes phase 1 in its request checksum and writes that checksum at
the complete transfer's tail. A checksum-valid empty phase-0 read advances
the N61 request tag.

The exact Apple T7000 kernel used to trace the clock, PMIC and startup
operations has SHA256
`0145bc0eda01cd55e8b996d24ffc19c25712714326f893ba5085a5016d30a4ad`.
The Gen1 firmware container follows the
[pinned Corellium implementation](https://github.com/corellium/projectsandcastle/tree/03db9c6ae04141eb940f3b9f56d446f50d57fadf/hx-touchd),
with the N61 clock values and startup behavior taken from that exact Apple
kernel. Private ADT/calibration bytes stay outside these patches.

Native N61 startup, firmware upload and touch reports passed physical
qualification during one experimental USB/NFS-rooted Arch session. Taps
and swipes produced evdev contact/release events and reached the compositor.
The N61 touch child is enabled with `ana-supply`, `core-supply` and `clocks`.
The separately gated preserved-power path requires a prior ROM response
and excludes those resource references. Repeated cold boots and return to
iOS await qualification; installation remains disabled.

`tests/test_n61_spi.py` checks the board/controller contract.
`tests/test_n61_touch.py` compiles the native packet and regulator code with
synthetic data to check wire bytes, transfer bounds, failed transfers and
the shared display bits across every possible starting register byte.

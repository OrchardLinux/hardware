# iPhone 6 touchscreen bring-up

N61 uses the native `apple_z2` input driver with a T7000 SPI controller profile,
PMGR touch clock and PMIC supply switches. The device selects the `apple-4k`
kernel. The board description lives in `kernel/patches/devices/t7000/`. The shared
driver patches live under `kernel/patches/drivers/`.

The tethered N61 session on 2026-10-28 accepted ROM readiness, uploaded the
model firmware and returned a valid surface descriptor over SPI. Linux exposed
`iPhone 6 Touchscreen`, and Labwc opened its evdev endpoint. A physical test
then captured 996 input events over 117 synchronized reports. All ten contacts
ended with releases. The user entered calculator digits and resized its window
through the panel, while the host captured the results. GPIO3 returned high
after the report queue cleared.

The complete native payload also passed a fresh tethered boot into Arch and the
Omarchy desktop that day. The standard module loaded automatically, and physical
input produced 11,668 events over 1,408 synchronized reports. Fifty contacts each
ended with a release. The user confirmed calculator input and swipes. This
capture reached x=0..747 and y=117..1150, with every slot inactive at the end.

On 2026-10-29, a reconciled kernel and private firmware combination reached the
desktop on two tethered boots. The first capture recorded 25 contacts and 25
releases, reaching y=26..1322 on the 0..1333 vertical axis. The owner entered
`n61` with the on-screen keyboard. Touch also worked on the second boot, with
18 contacts and 18 releases. The build identities are in the N61 bring-up record.

The native touchscreen child is enabled after this qualification. The profile
retains experimental status with installation disabled.

## Board controls

| Resource | N61 connection |
|---|---|
| SPI2 | `0x20a088000`, size `0x4000`, AIC interrupt 154 |
| SPI input clock | 60 MHz |
| Touch transfer policy | Mode 0, 8-bit words, 500 kHz during bring-up |
| Chip select | GPIO185, active low |
| Reset | GPIO2, active low |
| Touch interrupt | GPIO3, falling edge |
| Touch clock | PMGR offset `0x1e000`, 24 MHz parent, requested rate 32768 Hz |
| Core supply | D2186 at I2C0 address `0x74`, register `0x0085`, bit 1 |
| Analog supply | Chestnut at I2C0 address `0x27`, register `0x05`, bit 4 |

N61 reuses `chestnut-touch-hv` for the analog supply, including its 1000 us
enable time and off/on delay. The regulator changes register `0x05` bit 4.
The display's boot path leaves the shared boost and Sage rails enabled;
their bits 0 through 2 retain that state across touch enable and shutdown.
The T7000 clock uses the existing divider and gate operations. Startup enables analog and
core power, settles chip select, starts the clock, performs the pre-reset
exchanges and releases reset before checking synchronous ROM readiness.

The T7000 SPI configuration uses a word-size index shifted by 15. For 8-bit
mode 0 it programs `0x4018`, then adds PIO enable for `0x4038`. T8010 keeps its
existing controller policy. The N61 readiness check accepts the ROM words
established by Apple's driver; transfer framing must reproduce those bytes
exactly.

N61 interrupt reads use an EB data-phase request with its checksum in the final
two transfer bytes. A 61-byte read places that checksum at offsets 59 and 60.
The earlier fixed 16-byte placement left a report queued after a physical
contact. Correcting the native request restored contact and release events
while retaining the frame header and payload checks.

## Firmware and calibration

`devices/n61/firmware.json` pins `N61.mtprops` from iOS 12.5.1 build 16H22 and
uses the shared `z2fw-from-mtprops@3` converter. The output path is
`lib/firmware/apple/n61-fwstart.z2fw`, with SHA256
`51d2ef7b25c1d7e323fa703b531223312cb76f7183fbeb425f921dd6e5509294`.

The N61 personality resolves shared Apple XML integer references to reference
divider 2 and clock-32 enable 1. Preserve XML references when reading a kernel
personality; a flattened export can lose those values.

The model firmware carries calibration records. During boot, m1n1 forwards
`multi-touch-calibration` and `prox-calibration` from this phone's ADT through
the board's `touchscreen0` alias. The driver consumes `apple,z2-cal-blob` and
`apple,z2-prox-cal-blob`, bounded to 1024 and 128 bytes. Gen1 calibration uses
the full word count in its packet header.

Acquire firmware through the shared CLI with an explicit `n61` profile. Keep
the resulting private mixin and receipt together. The recorded N61 desktop build checked those inputs against the hardware
recipe before placing root-owned firmware in the private filesystem. The
companion CLI changes provide the converter. Firmware and unit calibration stay in
private build inputs.

Keep raw readiness replies, firmware provenance and evdev captures with the
private test record. The recorded physical test reached x=28..746 and
y=452..892 within the advertised 750 by 1334 coordinate space. Later boots
extended that coverage as recorded above.

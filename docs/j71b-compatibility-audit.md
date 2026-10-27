# J71b compatibility audit — issue #1

## Scope and result

Source-level review of the five `kernel/patches/j71b` patches, J71b m1n1
patch, quiet-boot patches, firmware recipe, and firmware staging/validation
path. Kernel baseline: `6831bc701a6ce059e71e5aaa9488c9195bea6927`.

The original Wi-Fi restriction came from a single-unit bring-up experiment,
not a requirement of J71b hardware. It should not have become a model-wide
support policy. This audit also found an upstream DART interface removal.
Both are corrected in the source. **This is not a claim of hardware-tested
support for every J71b variant, or proof that the entire patch set is
regression-free.**

## Corrections

| Finding | Correction |
| --- | --- |
| Wi-Fi accepted only `YSBC/u/4.3`. | Construct the required NVRAM filename from the full valid OTP tuple, without a single-unit allowlist or dependence on antenna SKU. |
| One generic J71b NVRAM filename concealed a particular profile. | Package separate `YSBC/u/4.3`, `YSBC/u/4.1`, `YSBC/m/2.5`, and `YSBC/m/2.3` files. No generic J71b NVRAM fallback. |
| A profile rejection surfaced as `-ENOMEM`. | Return and propagate `-EINVAL` for invalid OTP/board/seed inputs in probe and reset paths. Allocation failures still report `-ENOMEM`. |
| Upstream board-name and platform NVRAM fallbacks could select unrelated data after adding profiles. | Add an exact-file request flag for J71b NVRAM. Missing exact files cannot use board fallback, generic NVRAM, BCM47xx NVRAM, or EFI NVRAM. Other boards keep upstream behavior. |
| RX DMA workaround changed every brcmfmac device. | Use bidirectional RX mapping only on J71b; retain `DMA_FROM_DEVICE` elsewhere. |
| m1n1 required exactly 1780 calibration bytes, observed on the original unit. | Copy the nonempty calibration property using its ADT-provided length. Keep MAC validation, matching device MAC sources, and rejection of prefilled identity. The brcmfmac OF path already accepts a variable-length blob. |
| Bluetooth H4 registration depended on creation of a diagnostic debugfs capture. | Remove the experimental raw-RX capture entirely; restore upstream H4 behavior. |
| Bluetooth patch removed upstream autosuspend cleanup and reformatted the ACPI match table. | Restore both unchanged; retain the additional device quirks. |
| DART rejected upstream five-cell `iommus` specifiers and removed per-master DMA-window intersection. | Restore one-/five-cell parsing and per-master intersections, intersecting with the optional controller window used by J71b. Validate controller-window length, alignment, and overflow. |

The `m/2.5`, `m/2.3`, and `u/4.1` hashes were independently checked against
existing local extraction of the pinned 21H461 IPSW. The `u/4.3` pin is
unchanged from the working recipe. The two `m` files have identical contents;
the `u/4.1` hash equals the existing `u/4.3` pin. Separate names still preserve
explicit profile selection. No Apple firmware or device-private data is
added to the repository.

## Other patch areas reviewed

- **Touch, SPI, UART, HDQ/battery:** existing OF match names are retained;
  additional variant data selects legacy SPI status, T8010 oversampling,
  HDQ transport, and touchscreen protocols. The large touch/SPI refactors
  change shared code, so retaining match names alone cannot establish
  behavioral equivalence on upstream devices.
- **PCI power control:** recognition expands from the first compatible
  string to any PCI compatible string. Additional readiness checks support
  the new H9P driver. J172-specific bridge handling remains separately
  selected from T8010.
- **NVMe/H9P:** explicit J71b read-only/read-write modes, command filtering,
  failed-DMA retention, and guarded reprobe are intentional internal-storage
  safety policy, not Wi-Fi-profile restrictions. They remain in place.
  Runtime explicitly selects the appropriate mode; do not remove these
  controls merely to make the diff look additive.
- **SIO:** the firmware size/hash gate pins the recipe's common SmartIO image,
  not a device-unique calibration blob. Retained. The `diagnostics-only`
  naming is historical and should not be mistaken for a hardware variant.
- **GPIO wake and PMGR:** wake handling is limited to the added J71b s2idle
  path; clock-provider support and locking also touch shared PMGR code.
  Successful full suspend/resume remains unproven (and previously failed
  on the reference device).
- **Boot:** HMB comes from live firmware data. Quiet-boot patches are
  conditional presentation changes; serial/USB logging remains available.

## Remaining risks and limits

1. Bluetooth still uses the single FigaroA/USI HCD and J71b power sequence
   validated during bring-up. The Wi-Fi OTP vendor letter alone does **not**
   establish which Bluetooth image or power sequence another unit requires.
   Check Bluetooth initialization on the reporter's unit; do not silently
   declare it supported or substitute another HCD without evidence.
2. Shared DART, touchscreen, SPI, PMGR, and NVMe paths require compilation and
   regression testing. Source checks are not a guarantee of unchanged
   behavior on all Hoolock/Asahi hardware.
3. The retained J71b boot path expects `/arm-io/uart2/wlan`, matching the
   available bring-up evidence. Other ADT layouts need actual evidence and
   explicit handling, not copied MAC/calibration or guessed paths.
4. New Wi-Fi profiles have matching public NVRAM, but firmware startup,
   association, DHCP, traffic, and regulatory behavior still need hardware
   validation. No hardware was rebooted, probed, or written during this work.

## Regression checks

```sh
python3 kernel/audit-patches.py /path/to/local/pinned-linux-checkout
python3 -m unittest discover -s tests -p 'test_wifi_profiles.py' -v
```

`audit-patches.py` extracts only touched files from the pinned commit into a
temporary directory, applies the entire series in build order, checks that
existing OF match strings remain, and checks DART window contracts. It never
modifies the supplied checkout. The profile tests compile and execute the
actual added C guard/path-selection blocks with host stubs, including all
four profiles, a future profile, malformed/missing OTP, missing seed, wrong
board, allocation failure, and non-J71b behavior. Other checks cover exact
lookup/fallback guards, manifest hashes, DMA scope, and diagnostic removal.

Observed locally: complete kernel series application passed; 58 hardware
Python tests passed using the current checkout and a Python environment with
its dependencies.

### Reference-device validation (2026-10-27)

The current kernel series was built for ARM64 with all 56 staged modules and
booted on the `YSBC/u/4.3` reference J71b as
`7.3.0-rc1-orchard-j71b`. The qualified DTB was unchanged. The existing m1n1
binary was retained: **the m1n1 calibration-length change is not covered by
this boot test**.

- Installed candidate root reached systemd, labwc and Quickshell; installed
  hardware/boot-file hashes passed. APFS geometry remained unchanged.
- Wi-Fi reported OTP `YSBC/u/4.3`, loaded firmware and calibration, associated,
  obtained DHCP, and passed gateway traffic and a DNS/HTTPS download with the
  same SHA-256 as an independent host download. A local-host HTTP transfer
  timed out; sustained throughput is not qualified.
- Bluetooth controller registered and powered on; touchscreen input device
  registered. Pairing/audio and physical touch interaction remain unverified
  in this validation session.
- No panic/oops, observed DART faults, or storage/filesystem errors were found.
- The experimental deployment initially retained old-release charging and
  touch-activation helpers. Deploying both templates from this repository
  with the built release corrected that deployment error, without weakening
  the release checks. `image/runtime.py` already renders both correctly.
  Charging then stayed active without restarts and the kernel verified its
  guarded 425 mA input limit. The screen-on battery remained net discharging;
  this does not establish net charging or suspend/resume correctness.
- Remaining observed messages include battery-status errors during startup,
  PMIC device-tree cell warnings, diagnostic probe rejections, unused legacy
  USB gadget failure, Wi-Fi P2P failure and unavailable Bluetooth RFCOMM.
  Their regression status requires comparison with the baseline; they are
  not silently classified as harmless.

A subsequent conventional Orchard CLI installation used the updated m1n1,
kernel, firmware recipe and generated hardware helpers. It reached the
installed `/omarchy` desktop, passed hardware-file integrity verification and
Wi-Fi HTTPS checksum comparison, and had no failed systemd units or charging
helper restarts. The user successfully paired a Magic Keyboard. Bluetooth
advertising-packet warnings and the previously listed limitations remain.
This local image also contained unrelated preserved desktop edits; it is not
the clean-main distro release image and must not be published as that image.

This validates reference-device installation and basic operation, **not** all
J71b variants, full feature parity, or a complete release. The reporter's
`m/2.5` device, other profiles, variable-length calibration on other units,
cold-start shared launch and suspend/resume still need validation.

The updated firmware recipe requires a matching host hardware package. Release
the CLI with that dependency pinned, and raise consuming images' `min_orchard`;
an older host recipe cannot prepare the new recovery's required firmware set.

## Release and device validation

Rebuild and publish matching kernel/modules, m1n1/boot assets, recovery, and
hardware package. The firmware manifest is embedded in recovery and used for
strict file/hash validation; a new host recipe paired with old recovery will
correctly reject the changed file set. Rebuild installed-system artifacts as
well: fixing recovery alone leaves the installed system's old driver.
Existing prepared firmware receipts include the manifest digest and must be
regenerated. The CLI's staging and recovery runtime enumerate the recipe;
no fixed seven-file implementation needs changing.

Test both the existing `u/4.3` reference device and the reported `m/2.5` device:

- Verify matching release assets, OTP tuple, exact selected NVRAM, firmware
  startup, and device-local MAC/calibration (log presence, not private bytes).
- Verify missing exact NVRAM fails without generic or alternate-profile
  fallback; unknown profiles must not inherit another unit's NVRAM.
- Verify passive scan, association, DHCP/DNS/HTTPS, and no DART faults.
- Verify Bluetooth, touch, storage safety modes, and installed-system boot.
- Preserve the working baseline and obtain user agreement before DFU/reboot
  or internal-storage writes. Compilation alone does not close issue #1.

## Policy for future changes

Device support must be selected by documented compatible/OTP/ADT data, not
by a single reference unit's values. Preserve existing match entries,
interfaces, and default behavior outside the new target. Scope hardware
quirks narrowly. Document intentional safety restrictions separately from
compatibility limits. Any removed behavior requires an explicit rationale
and regression test; a bring-up experiment is not sufficient justification.

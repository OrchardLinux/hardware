# J81 follow-up qualification — 2026-10-29

The requested quiet-versus-normal Pongo comparison is complete: **both pinned
source builds returned to iPadOS without a Pongo USB endpoint**. Removing
`QUIET_BOOT` did not fix the source-built loader. The known-working Hoolock
prebuilt remains the control; the underlying source-build failure is unresolved.

Separately, the full Omarchy A v0.1.1 desktop was installed to this J81's internal
ext4 partition and returned after a clean reboot and fresh Pongo/m1n1 boot.
Global PID 1 was systemd, the persistent counter advanced to 2, an exact fsynced
owner marker survived, and Wi-Fi/DNS/HTTPS passed. This was an experimental
contributor kernel/module/bootstrap, **not a test of ANS1 installation in the
unchanged Orchard main kernel**. The shipped J81/J82 installation flags remain
unchanged, and no J82 qualification is implied.

| Evidence | Scope |
| --- | --- |
| [Pongo comparison](pongo/COMPARISON.md) | Fixed source/toolchain/patch; only quiet flag differs; forced rebuilds and two hardware rounds |
| [Paired outcomes](pongo/live-comparison.json) | No endpoint or Linux upload for either source-built Pongo; no storage writes in these rounds |
| [Rebuild receipt](pongo/rebuild-receipt.json) | Quiet/normal/quiet outputs reproduce; changing flags alone is not a Make dependency |
| [Source identities](pongo/source-provenance.json) | Pinned Pongo/newlib, patch/source hashes and toolchain context |
| [Preprocessor comparison](pongo/preprocessor-comparison.json) | Framebuffer/entry differ; sampled USB/shell units match at source level, without a runtime-equivalence claim |
| [Native-root report](NATIVE-ROOT.md) | Installation, first global systemd boot, fixes and fresh-boot persistence |
| [Installation result](native-install-result.json) | Full original archive transfer/extraction, native tools, exact storage extent and desktop/network checks |
| [Fresh-boot result](native-reboot-result.json) | Distinct kernel boot, counter 2, exact surviving marker, unchanged SSH identity and guards |
| [Native image receipt](NATIVE-BOOTSTRAP-BUILD-VERIFIED.json) | Exact qualified native artifact hashes; kernel/DTB/loader/bootargs retained |

## Fixes and remaining work

- Native e2fsprogs and blkid need absolute `/usr/bin/` paths: BusyBox standalone
  shell otherwise selects its applets despite PATH. The first option rejection
  happened before formatting; native RAM-file preflight preceded continuation.
- Interactive ash ignored SIGTERM, leaving PID 1 waiting. Verifying and killing
  only the exact disposable installer-shell child released the pending handoff.
- The first automatic native bootstrap stalled in BCM4350 probe before recovery
  USB. Deferring that Wi-Fi modalias to installed systemd/udev allowed the next
  boot to reach the desktop, then reconnect Wi-Fi. This is measured ordering
  evidence, not a diagnosis of every possible Wi-Fi/firmware stall.
- Cold boot is still host-tethered, with retained Apple boot support. Only
  gpio-keys was detected as native input. Touch, audio, Bluetooth, GPU
  acceleration, suspend, charging policy and long-duration reliability remain
  unqualified. Source-built Pongo remains unresolved.

## Source and test boundaries

The original refactored baseline was hardware
`e2b1b591b495ae479951b06c18b99d3c86a0a7a9`, tested before experimental changes.
The ANS1 work uses kernel `7.3.0-rc1-orchard-j81-storage-ro-v2mod` and its exact
matching range-gated module, as recorded in the native report. The underlying
ANS1 work is Nick Chan's, reconciled/hardened through the Hoolock/iPad NixOS
work; attribution and licenses remain with the source.

Experimental overlays, development device trees, host controllers and private
payload preparation stay in the pinned
[contributor source](https://github.com/micro23/ipadair2-omarchy/tree/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/experimental/j81-storage),
as CONTRIBUTING.md requests. Its 48 guard/parser tests passed, with synthetic
GPT/APFS fixtures rather than owner metadata. The reproduction helper and
source-level Pongo diffs also remain on that fork. These receipts describe the
exact previously tested artifacts; this documentation PR changes no runtime,
kernel, device support flag or installer behavior.

No Wi-Fi credentials, SSIDs, ECIDs, filesystem/partition UUIDs, raw APFS/GPT
blocks, private SSH keys, owner firmware or built images are included. The
[contributor source audit](contributor-source-privacy-audit.json) applies to
that pinned source publication, not a claim to have tested arbitrary upstream
history. Evidence-file checksums can be checked from this directory with
`shasum -a 256 -c SHA256SUMS`.

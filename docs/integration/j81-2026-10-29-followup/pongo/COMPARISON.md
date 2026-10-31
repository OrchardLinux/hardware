# J81 Pongo: quiet versus normal output — 2026-10-29

**Both locally built variants failed to expose a Pongo USB endpoint on this J81
and the owner observed iPadOS returning. Removing `QUIET_BOOT` did not fix the
source-built loader.** This comparison does not identify the underlying cause.
The pinned Hoolock prebuilt remains the hardware-proven input.

## Inputs held fixed

- PongoOS: `checkra1n/PongoOS` `bb492b004265ce91123caa23b8bc04b2eff6d2b7`.
- newlib: `f9ea5054de8fb51dff6f6d3c2e7cdd4aa89744b8`.
- Identical Orchard [quiet source patch](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/docs/pongo/pongo.patch), present in both trees.
- Apple clang `21.0.0 (clang-2100.3.34.2)`, full iPhoneOS SDK `27.1`.
- Same source tree and cached newlib libraries. The final Pongo target was
  forced to rebuild for each run; changing `PONGO_CFLAGS` alone is not a Make
  dependency and must not be mistaken for a rebuild.
- The only build-flag difference: `PONGO_CFLAGS=-DQUIET_BOOT` versus empty
  `PONGO_CFLAGS`. The normal variant still has the conditional patch in its
  source, with its quiet branches inactive.
- Same original Wi-Fi J81, Mac, pinned palera1n/libusb runtime, USB path and
  guided DFU/download-mode procedure. Each round started from normal iPadOS.
  Cable reconnects were manual, not electrically timed by a test fixture.

| Input | Size | SHA-256 | Hardware observation |
| --- | ---: | --- | --- |
| Quiet source build | 238104 | `c6aa270ba22d40036487b027d5bb9b67955c35def61ceb65cff2b358dd40d80b` | DFU and download mode reached; `Booting PongoOS` printed; no Pongo USB endpoint; owner saw iPadOS return |
| Normal source build | 238104 | `90086685c73a066a05b736cc1908308ba2cda2a5283cdfebc1f7d9b62d41234d` | DFU and download mode reached; `Booting PongoOS` printed; no Pongo USB endpoint; owner saw iPadOS return |
| Previously proven Hoolock prebuilt | 238096 | `1e5543fd8e6dbd84c334b87d71aa473f4d347c2ba8a5e863b6e10f18461c7575` | Repeated earlier Pongo enumeration and Linux boots on this J81, including the ANS1 read-only session |

The prebuilt is a reference control from earlier successful sessions, not a third
round of today's paired comparison. Its compiler settings and complete binary
provenance have not been reconstructed; its different size is not a diagnosis.

## Fresh paired hardware test

The quiet round reached `Checkmate` at 17:29:31 EDT, download mode at 17:29:40,
and `Booting PongoOS` at 17:29:40. The normal round reached `Checkmate` at
17:31:49, download mode at 17:31:54 and `Booting PongoOS` at 17:31:55. Neither
produced a Pongo USB endpoint within the bounded observation. The owner reported
return to normal iPadOS for both. No Linux image was uploaded in these rounds;
the optional read-only `help` command was never sent because the endpoint was
absent. No storage probe or internal write occurred.

The full-Xcode quiet build is byte-identical to the earlier quiet build made
when the default SDK lookup produced a warning. The new quiet/normal/quiet
rebuild reproduced both recorded hashes; a second run of the published rebuild
helper reproduced them again. This rules out attributing the observed failure
solely to that earlier SDK-selection warning. It does not establish compatibility
of this compiler/newlib combination with the device.

See [fresh hardware outcomes](live-comparison.json),
[rebuild receipt](rebuild-receipt.json),
[reproduction-helper receipt](public-helper-receipt.json), and
[source identities](source-provenance.json). Raw host logs and device identifiers
remain private.

## What the flag actually changes

All `QUIET_BOOT` uses in this patched source are in two files:

1. `src/drivers/framebuffer/fb.c`: `screen_putc` returns without drawing text;
   `screen_init` fills black instead of drawing the normal logo.
2. `src/kernel/entry.c`: skips the fallback `screen_fill_basecolor` call.

Fresh `clang -E -P` comparisons found identical preprocessed source for
`synopsys_otg.c`, `usbloader.c`, `main_task.c`, `support/io.c` and `shell/main.c`.
The framebuffer and entry units differ as expected. See the
[preprocessor hashes](preprocessor-comparison.json) and the
[framebuffer](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/docs/pongo/fb-preprocessed.diff)/[entry](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/docs/pongo/entry-preprocessed.diff) diffs.

This is source-level evidence. With LTO, changed code layout, timing, cache
behavior or framebuffer memory access may still affect runtime. Identical USB
source is not proof of identical hardware behavior. Neither variant's failure
proves the quiet patch is harmless, and the normal build is not a demonstrated
fix. Do not replace the proven loader based on the flag comparison alone.

## Reproduce the build comparison

Use a checkout of the pinned Pongo revision, initialize its pinned newlib, and
apply `pongo.patch`. Follow upstream's newlib/toolchain setup, then run on the Mac:

```sh
python3 /path/to/ipadair2-omarchy/docs/pongo/rebuild_compare.py \
  --source /path/to/PongoOS \
  --output /path/to/new-comparison-output \
  --developer-dir /Applications/Xcode.app/Contents/Developer
```

The helper verifies the source and submodule identities, the modified file set,
and source hashes; it builds quiet, normal, then quiet again. It never opens a
USB device or executes a payload. Generated binaries are excluded from this
source repository. The reproduction helper, patch and source-derived diffs stay on the contributor
fork, in accordance with CONTRIBUTING.md. See its [NOTICE](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/NOTICE)
and [retained PongoOS license](https://github.com/micro23/ipadair2-omarchy/blob/11463ba09f5fbc0d1d5afb43d29f7ebe8c9eb3ce/LICENSES/PongoOS-LICENSE.md) for component terms.

## Follow-up for the maintainer

The quiet flag is not sufficient to explain the current source-build failure.
The next useful comparison is the known-working prebuilt's actual build
provenance/toolchain against these pinned source builds, or an early-stage
instrumented build that can explain the reset before USB enumeration. A
source/build-output or device-reset explanation is still needed; suppressing
framebuffer output or changing the SDK selection is not an established fix.

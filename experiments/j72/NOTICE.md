# Source provenance and licenses

Existing source notices and SPDX identifiers are preserved. New J72 C/Python
code and tests are GPL-2.0-only, accompanied by
[the kernel license text](LICENSES/GPL-2.0.txt).

- `spi/spi-apple.c` and its baseline retain the Asahi Linux Contributors and
  SiFive notices. S5L support and earlier PIO work came through
  [mattonik/ipad-nixos at b94d42f](https://github.com/mattonik/ipad-nixos/tree/b94d42f78e04c5465e290340a5b6879bbc5956b6),
  whose imported patch files are recorded by Martin Puškáč. The J72
  corrections and lab baseline are identified separately by source hashes
  in [PROVENANCE.json](PROVENANCE.json). The preserved baseline is a local
  development source, not a claim that the reference commit contains every
  baseline byte.
- The HDQ UART frontend and stop-bit API derive from that same Air 2 work.
  The J72 adaptation restricts identification/writes, retains the reference
  decoding threshold and fixes contiguous echo matching. The source
  comments record those differences. Common serial-core context retains
  its existing Linux authorship; this release does not claim those files
  were written by this project.
- The J72 worker/decoder, bounded diagnostics, policy tests and packaging
  helpers were developed in this bring-up. N1 report framing also used
  Orchard's public input-driver work; the worker records the compared
  revision. Public Corellium Z2 work was another protocol reference, as
  described in the [process record](https://github.com/thisiscamk/ipad-air-j72-linux/blob/090c68e/PROCESS.md).

No Apple firmware, calibration arrays, captured device trees, private test
captures, credentials, prebuilt modules or root/boot images are distributed.

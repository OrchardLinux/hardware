# Publishing CLI releases

The bootstrap installs `releases/latest/download/requirements.txt`. This file selects immutable, hashed wheel URLs for a compatible CLI/hardware pair; the bootstrap has no package version pins.

## GitHub Actions

`.github/workflows/build.yml` tests on Python 3.11 and 3.14 for pull requests and `main`. Until hardware 0.1.11 is published, those development checks build its wheel from a pinned hardware source revision. That source wheel does not include the prebuilt loaders in a published hardware wheel. Tag and manual builds use a published hardware wheel with verified release checksums, or an explicitly selected hardware workflow artifact. They default to hardware v0.1.11, which must be published before a release build can use it. Manual dispatch also lets you choose a hardware tag.

A version tag matching `pyproject.toml` creates a **draft** release containing the wheel, sdist, hashed `requirements.txt` and `SHA256SUMS`. Manual builds only retain workflow artifacts for seven days. The hardware release must be published first; cross-repository drafts are not public dependency URLs. Nothing publishes automatically or operates a device.

Public tag/manual builds attest all final files, including `SHA256SUMS`, and retain `cli-build.sigstore.jsonl` with workflow and release assets. Private or unknown-visibility builds skip signing and bundle copying. Tests have no signing permissions; a separate finalization job signs only after the test matrix passes. A signing failure blocks public release creation. The bundle itself is not listed in the signed checksum manifest. Verify a downloaded file with `gh attestation verify FILE --repo OrchardLinux/cli`.

## Build wheels

In each repository separately:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip build
python -m build --wheel
```

Build hardware first, then CLI. Wheel versions come from each repository's `pyproject.toml`. The release pair prepared here is CLI `0.1.10` and hardware `0.1.10`. It includes the expanded J71b Wi-Fi firmware recipe; consuming images must require Orchard `0.1.10` or newer.

## Prepare assets locally

From the CLI repository, pass explicit paths to both wheels:

```sh
python tools/prepare_release.py \
  --cli-wheel dist/orchard_cli-0.1.10-py3-none-any.whl \
  --hardware-wheel /path/to/orchard_hardware-0.1.10-py3-none-any.whl \
  --output out/publication/host-0.1.10
```

Use the actual wheel filenames for later versions. The tool reads their metadata and computes hashes. Output must be a new directory:

- `hardware/`: wheel for the hardware repository's `vVERSION` release.
- `cli/`: CLI wheel and `requirements.txt` for the CLI repository's `vVERSION` release.

Before publishing, install the pair into a fresh virtual environment, run `python -m pip check`, run CLI tests from this repository, and resolve a release without device access:

```sh
python3 -m venv out/release-test
out/release-test/bin/python -m pip install --only-binary=:all: \
  dist/orchard_cli-0.1.10-py3-none-any.whl \
  /path/to/orchard_hardware-0.1.10-py3-none-any.whl
out/release-test/bin/python -m pip check
PYTHONDONTWRITEBYTECODE=1 out/release-test/bin/python -m unittest discover -s tests -v
out/release-test/bin/orchard resolve tests/fixtures/first/index.toml
```

Also run the hardware tests from its own repository using the same installed wheel pair. Keep published wheel URLs immutable; changed contents require a new version.

## Publish in dependency order

1. Publish the hardware wheel/components at their immutable tag; retain the previous complete hardware release as latest.
2. Publish the matching CLI wheel and generated `requirements.txt` at their immutable tag.
3. Build consuming distro images with that exact pair and the new minimum CLI version.
4. Promote the complete hardware, compatible CLI and distro releases to latest in that order. Their `latest/download` URLs must remain usable throughout.
5. Resolve the hosted distro index and qualify installation and cold-restart launch on supported hardware before announcing the release.

See Omarchy A's release instructions for distro assets. Local preparation does not upload assets or operate a device.

## Update behavior

- **CLI/hardware:** rerunning the bootstrap uses `pip install --upgrade` against the latest requirements. Dependency resolution checks the selected pair.
- **Host tools:** `setup-host` only installs missing palera1n/ipsw executables; existing installations remain user-managed.
- **Distro metadata:** registered URLs are fetched on every resolution. Verified images are reused by hash.
- **Installed system:** this does not implement a distro upgrade or update the device filesystem in place.

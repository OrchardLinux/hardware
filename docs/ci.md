# GitHub builds

`.github/workflows/build.yml` tests the installed wheel on Python 3.9 and 3.12 and runs a native ARM64 compile/execute smoke test for pull requests and `main`. Tags matching `v*` and manual dispatches additionally build:

- Linux ARM64: kernel, modules, DTB, static recovery tools and quiet m1n1.
- macOS: quiet Pongo with the pinned newlib revision.
- Assembly: wheel/sdist, `j71b-components.tar.gz`, source archives and `SHA256SUMS`.

The standard GitHub job image is Debian Bookworm, matching the local toolchain base. There is no nested Podman invocation. Native jobs set `ORCHARD_BUILD_NATIVE=1` and `ORCHARD_BUILD_ENV_ID`, run the same `install-toolchain` files used by the Containerfiles, and execute the same compiler recipes. Shared `ORCHARD_*` recipe variables select native filesystem paths in CI and mounted paths locally. Native execution is not a security sandbox: it does not enforce read-only inputs or isolate networking. Native mode is only for trusted, disposable Linux ARM64 workers. Local builds continue to use Podman by default.

GitHub must offer the `ubuntu-24.04-arm` runner for the repository. Kernel builds with debug information need substantial disk space and can take hours; the workflow timeout is six hours. The workflow records compiler package versions in the component bundle. Signed package repositories can change between builds, as with local builds; this is not a claim of byte-for-byte reproducibility.

## Build efficiency and runner costs

The distribution is built once per run and the identical wheel is installed by both test versions, the native smoke test and the compiler jobs. No job rebuilds a dependency from this checkout. Kernel and tools/loader are separate jobs, so **rerun failed jobs**, not the entire workflow, when an unchanged run can be recovered. Final assembly consumes their artifacts without recompiling. The combined tools installer takes the union of the local recipes' package lists and runs one APT transaction.

Pongo/newlib compile once on macOS. An exact-input cache includes its source pins/build script, patch, runner OS/architecture, Xcode, SDK and compiler fingerprint; unchanged Apple inputs reuse the binary **and corresponding source archive**. No broad fallback cache or kernel object cache is used. Compiler jobs only run for tags/manual dispatch; documentation-only pushes do not start CI.

Standard GitHub-hosted Linux ARM64 and macOS runners are free for public repositories. Included private-repository minutes are not a limit on these public standard-runner builds; larger runners are always billed. Artifact/cache storage is separate, so intermediate artifacts expire after two days and the Pongo cache is limited to its small, exact-input outputs. See [GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Record measured job/step durations from the run API, including cache hits, rather than treating workflow wall time as the sum of concurrent runner minutes. Keep the first hosted-run validation and timings in `docs/ci-validation.md` once the build has been exercised.

## Release behavior

Manual dispatch uploads two-day workflow artifacts only. A version tag must match `pyproject.toml`; after successful builds it creates a **draft** release without overwriting existing assets. Review and publish dependencies deliberately. Builds do not access devices or qualify storage/boot behavior.

Publish in this order:

1. Hardware wheel, component bundle, checksums and corresponding source.
2. A compatible CLI release, selecting that hardware tag.
3. Minimal Linux and/or Omarchy A images, selecting the explicit hardware/CLI tags.
4. Shared launcher, using the exact published roots and their build receipts.
5. Qualify the complete installation/cold-restart flow before announcing the app.

Cross-repository inputs must be published public releases, not drafts. The default repository token cannot read another repository's drafts. No broad personal access token is required for public dependencies.

## Artifact attestations

Public tag and manual builds attest every final asset, including the checksum manifest, with GitHub build provenance. The hardware and shared-launcher bundles are retained as `hardware-build.sigstore.jsonl` and `hardware-launcher.sigstore.jsonl` alongside workflow artifacts and draft/attached release assets.

Attestation and bundle-copy steps require repository visibility to be explicitly public. Private (or unknown-visibility) builds skip both steps; no signing token is requested and no attestation is sent to Sigstore. This follows the same public/private policy as Resize APFS. Signing permissions are scoped to the final assembly job, not tests or release-upload jobs. A public signing failure prevents the release upload.

The bundle is created **after** signing the checksum manifest, so it is deliberately not listed inside that manifest. Verify an asset against this repository's attestation API with:

```sh
gh attestation verify j71b-components.tar.gz --repo OrchardLinux/hardware
```

Use the expected repository identity, not just a matching hash. Attestation identifies the workflow/commit and bytes; it does not establish reproducibility or physical-device qualification. See [GitHub's attestation documentation](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations).

## Shared launcher

Run `.github/workflows/launcher.yml` manually after the image releases exist. Select:

- The hardware and compatible CLI tags.
- A Minimal Linux tag (supplies its signed Alpine base and compatible root).
- Optionally an Omarchy A tag, built with the same kernel/modules/runtime.

The producer validates component inventories, quiet-patch receipts, root streams and kernel/runtime compatibility. It refuses incompatible roots rather than advertising unsupported installations. For the J71b update, rebuild both Minimal Linux and Omarchy A with hardware/CLI v0.1.10 and include both new roots. Before checksumming and attesting, the workflow sets both launcher manifests' `min_orchard` to the selected CLI tag so older host firmware recipes are rejected before preparation.

By default the workflow only uploads `shared-launcher-assets`. Explicitly enable `attach_assets` to attach new assets to the selected hardware release. This never overwrites existing assets, changes the latest-release selection, or publishes a draft. The wheel/component checksums stay immutable; launcher assets have a separate `launcher.SHA256SUMS`.

Keep the completed hardware release selected as latest so the device descriptor's `latest/download/j71b-launcher.boot.toml` URL works. A future wheel-only release must not displace it until its own matching launcher is complete.

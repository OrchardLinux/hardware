# Releases

Until the installer that ships with each distribution release exists, users install
the released Orchard 0.1.11: the `requirements.txt` of the
[cli releases](https://github.com/OrchardLinux/cli/releases) selects the paired
CLI and hardware wheels. Those releases stay as they are.

This repository's releases (a `v*` tag, see [GitHub builds](ci.md)) publish the
component bundles, the loaders `m1n1.bin` and `pongo.bin`, source archives,
`SHA256SUMS` and the attestation bundle. They publish no Python package: `orchard`
runs from a checkout. The installer for distribution releases is designed in the
tooling plan and replaces the wheel pair.

Publish deliberately: a tag creates a **draft** release; `publish.yml` checks
the checksums and the build attestation before publishing the exact assets.
Qualify installation and cold-restart launch on supported hardware before
announcing a release.

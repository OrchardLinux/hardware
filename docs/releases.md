# Releases

Every distribution release carries its installer, `install.sh` and
`orchard-install.pyz` (orchard's core, the release's index and the device data and
loaders of its boards), made by `orchard.installer release` from the orchard
release the distribution pins. Releases built before orchard 0.2 install with the
released Orchard 0.1.10: the `requirements.txt` of the
[cli releases](https://github.com/OrchardLinux/cli/releases) selects the paired
CLI and hardware wheels. Those releases stay as they are.

This repository's releases (a `v*` tag, see [GitHub builds](ci.md)) publish the
component bundles, the loaders `m1n1.bin` and `pongo.bin`, `orchard-core.pyz`
(the core with every device, which `orchard update` installs), source archives,
`SHA256SUMS` and the attestation bundle. They publish no Python package: `orchard`
runs from a checkout for development, and as the core for users.

Publish deliberately: a tag creates a **draft** release; `publish.yml` checks
the checksums and the build attestation before publishing the exact assets.
Qualify installation and cold-restart launch on supported hardware before
announcing a release.

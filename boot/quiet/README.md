# Quiet boot patches

`m1n1.patch` and `pongo.patch` clear the screen to black and disable
framebuffer text in m1n1 and Pongo, so a boot shows no loader branding. USB and
serial diagnostics stay available.

The hardware build applies them: `--extra-patch boot/quiet/m1n1.patch` for
m1n1, and `build-pongo.py` for Pongo (see `.github/workflows/build.yml`).

A build or unit test is not visual qualification. Check the black screen on
supported hardware before announcing a release.

## The one Pongo

`build-pongo.py` builds the only Pongo Orchard ships. Distro releases, `orchard
launch` and local builds all use it; distros don't build their own. Distro
branding is drawn by m1n1, after Pongo. The hardware release carries it as
`quiet/pongo.bin` in the component bundles and as a release asset of its own, and
publishes its source as `pongo-source.tar.gz`.

The Pongo revision must include upstream `ad8b2d1` ("Fix for Xcode 16.3: don't
depend on section order"). Older revisions built with Xcode 16.3 or later copy
only part of the image into SRAM, so Pongo never reaches USB: the iPad goes back
to iOS or palera1n fails with `softboot e0004051`. Berkay Kösebay found and
tested this on the iPad 5 (OrchardLinux/omarchy-a#22).

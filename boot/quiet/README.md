# Quiet boot patches

`m1n1.patch` and `pongo.patch` clear the screen to black and disable
framebuffer text in m1n1 and Pongo, so a boot shows no loader branding. USB and
serial diagnostics stay available.

The hardware build applies them: `--extra-patch boot/quiet/m1n1.patch` for
m1n1, and `build-pongo.py` for Pongo (see `.github/workflows/build.yml`).

A build or unit test is not visual qualification. Check the black screen on
supported hardware before announcing a release.

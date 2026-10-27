# Build hardware components

For native GitHub Actions builds and release ordering, see [GitHub builds](docs/ci.md). Local builds below keep Podman as the default.

Run commands from this repository. Requires Python 3.9+ with venv/pip. Kernel and recovery-tool builds use Podman; loader builds also require the tools listed in their producer scripts.

## Build and install the package

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip build
python -m build --wheel
python -m pip install dist/orchard_hardware-0.1.9-py3-none-any.whl
```

Use the wheel filename matching `pyproject.toml` for later versions. The wheel includes device data, runtime code, kernel patches/configuration and producer resources. It has no CLI or distro dependency and contains no private firmware, ADT or device identifiers.

## Kernel and loader

Find the installed resource directory:

```sh
HW=$(python -c 'from pathlib import Path; import orchard_hardware; print(Path(orchard_hardware.__file__).parent)')
"$HW/kernel/build" j71b --output "$PWD/out/kernel"
"$HW/boot/m1n1/build" j71b --output "$PWD/out/m1n1.bin"
```

Choose new output paths. Producers use pinned upstream revisions, hardware patches and public toolchains. Their `.work` cache lives beside the installed producer, so use a writable virtual environment. Kernel/module builds enforce matching kernel releases. See [kernel/README.md](kernel/README.md) and [recovery tools](boot/initramfs/j71b/recovery/README.md) for individual producers.

The m1n1 backend is unbranded by default. A distro can pass `--extra-patch FILE`, `--include-header FILE`, and `--extra-cflags FLAGS` for its own presentation.

## iPad mini 1 components

The [P105 hardware profile](devices/p105/README.md) covers the first-generation Wi-Fi iPad mini. The [P105 producer](kernel/p105/README.md) builds ARMv7 public kernel components with an empty initramfs, or a private boot bundle from an explicit owner root and calibration. Run `kernel/build p105 --work NEW_WORK --output NEW_OUTPUT`; both directories must be outside this package. Its desktop configuration and device tree disable the reference NAND controller.

The manual boot route uses an existing jailbreak and a root served over USB/NFS by the host. Follow [systemd runtime integration](runtime/p105/README.md) for the installed root. A private bundle embeds per-user recovery inputs and stays on the owner's host. The J71b release workflow continues to build J71b artifacts; P105 launcher publication and CLI integration are separate work.

## Runtime and image assembly

- `image/runtime.py`: modules, hardware/touch scripts, device API and integrity lists.
- `image/prepare_runtime.py`: Python/ELF dependency staging and recovery root-stream receipt.
- `image/recovery.py`: recovery assembly, newc packing and m1n1 framing.
- `runtime/j71b`: archive containment, staging cleanup, storage fencing and PID-1 handoff.
- `devices/j71b/touch/runtime`: device activation scripts.

Image builders pass a `--policy-dir` containing `image_policy.py` and supporting modules. The policy is staged into the image, not imported on the host. It supplies `ready`, `validate_root`, `validate_handoff`, `verify_privileges`, `verify_session_access`, `diagnostics`, and `INIT`; diagnostic retirement also uses `stop_diagnostics` and `DIAGNOSTICS_EXECUTABLE`. Desktop and init policy belong to the image, not the shared hardware runtime.

Recovery and installed roots receive identical public runtime files and integrity lists. Runtime paths are `/run/orchard`, `/etc/orchard`, and `/usr/libexec/orchard-j71b`; the command endpoint is `/usr/bin/orchard-device`.

## Shared launcher and publication

See [black-screen shared launch](boot/quiet/README.md) for building and publishing the launcher manifest, recovery and Pongo assets. These are required for the default `orchard launch` flow; publishing a wheel alone is insufficient.

Upload the hardware wheel to its immutable `vVERSION` GitHub release. Publish the compatible CLI pair using the [CLI release instructions](https://github.com/OrchardLinux/cli/blob/main/docs/releases.md). Retain upstream licenses and corresponding source/build inputs when distributing compiled components.

## Test

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Unit tests do not qualify physical-device boot, storage operations or display behavior. Validate those on supported hardware before announcing a release.

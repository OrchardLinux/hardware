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

Choose new output paths. Producers use pinned upstream revisions, hardware patches and public toolchains. Their `.work` cache lives beside the installed producer, so use a writable virtual environment. Kernel/module builds enforce matching kernel releases. See [kernel/README.md](kernel/README.md) and [recovery tools](boot/initramfs/recovery/README.md) for individual producers.

The m1n1 backend is unbranded by default. A distro can pass `--extra-patch FILE`, `--include-header FILE`, and `--extra-cflags FLAGS` for its own presentation.

## Runtime and image assembly

- `image/runtime.py`: modules, public firmware data and integrity lists for both roots; the recovery's runtime.
- `image/prepare_runtime.py`: Python/ELF dependency staging and recovery root-stream receipt.
- `image/recovery.py`: recovery assembly, newc packing and m1n1 framing.
- `runtime/recovery`: the recovery's `orchard-device` (`bootstrap`, `status`), boot identity and the read-only storage fence. Installing is the host's install script over SSH; the recovery never hands over to an installed root.

Both roots get the same modules, public firmware data and integrity list. Only the recovery gets the runtime, at `/usr/libexec/orchard` with `/usr/bin/orchard-device`; its state is in `/run/orchard`.

## Publication

Upload the hardware wheel to its immutable `vVERSION` GitHub release. Publish the compatible CLI pair using the [CLI release instructions](https://github.com/OrchardLinux/cli/blob/main/docs/releases.md). Retain upstream licenses and corresponding source/build inputs when distributing compiled components.

## Test

```sh
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Unit tests do not qualify physical-device boot, storage operations or display behavior. Validate those on supported hardware before announcing a release.

# Build components

For native GitHub Actions builds, see [GitHub builds](docs/ci.md). Local builds below use Podman by default.

Run commands from a checkout of this repository. Kernel and recovery-tool builds use Podman; loader builds also require the tools listed in their producer scripts. The recipes need only Python 3.11+ and its standard library, run as `$ORCHARD_PYTHON` (default `python3`); `orchard` passes its own interpreter.

## Kernel and loader

```sh
./kernel/build j71b --output "$PWD/out/kernel"
./boot/m1n1/build j71b --output "$PWD/out/m1n1.bin"
```

Choose new output paths. Producers use pinned upstream revisions, the patch layers in this repository and public toolchains. The kernel keeps its source tree and compiler cache in the Orchard cache (see [kernel/README.md](kernel/README.md)). The other producers keep a `.work` directory beside the recipe unless given a work directory; `orchard build` passes directories in its cache. Kernel/module builds enforce matching kernel releases. See [kernel/README.md](kernel/README.md) and [recovery tools](boot/initramfs/recovery/README.md) for individual producers.

The m1n1 backend is unbranded by default. A distro can pass `--extra-patch FILE`, `--include-header FILE`, and `--extra-cflags FLAGS` for its own presentation.

## Runtime and image assembly

- `src/orchard/image/runtime.py`: modules, public firmware data and integrity lists for both roots; the recovery's runtime.
- `src/orchard/image/prepare_runtime.py`: Python/ELF dependency staging and recovery root-stream receipt.
- `src/orchard/image/recovery.py`: recovery assembly, newc packing and m1n1 framing.
- `runtime/recovery`: the recovery's `orchard-device` (`bootstrap`, `status`, `start`), boot identity and the read-only storage fence. Installing is the host's install script over SSH. Right after an install, `start` lets PID 1 switch into the new root when its boot entry boots the running kernel, so the first start needs no second DFU boot; later starts go through the entry.

Both roots get the same modules, public firmware data and integrity list. Only the recovery gets the runtime, at `/usr/libexec/orchard` with `/usr/bin/orchard-device`; its state is in `/run/orchard`.

## Loaders

`orchard launch` starts a device from its boot partition with only Pongo and the storage-capable m1n1. Both are built once for every Apple device. The release workflow publishes the quiet m1n1 and Pongo it built as release assets (`m1n1.bin`, `pongo.bin`); a checkout has none in `loaders/`, so `orchard` builds both from the source. Retain upstream licenses and corresponding source/build inputs when distributing compiled components.

## Test

```sh
python -m pip install -e .
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

Unit tests do not qualify physical-device boot, storage operations or display behavior. Validate those on supported hardware before announcing a release.

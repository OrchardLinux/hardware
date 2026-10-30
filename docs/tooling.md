# Orchard tooling: repositories, development, builds and the installer

Design agreed in October 2026. Sections marked **done** are implemented; the rest is the plan.

## Who uses it

1. **A user installs a distribution** on a supported device, with as little friction as possible.
2. **The same user starts it again.** A tethered boot needs a host tool on every boot.
3. **Someone brings up a device or a driver.** The most common development work. It changes kernel patches, device data, recipes and often host code, and needs no distribution.
4. **Someone works on a distribution** (the desktop, its installer), with mainline orchard or with orchard changes of their own.
5. **Someone creates a distribution.** Rare; minimal-linux is the reference to start from.

Changes are frequent: many commits a day across orchard and the distributions, most of them never released on their own. Nothing in daily work may depend on version numbers or pins.

## Repositories (done)

- **[OrchardLinux/orchard](https://github.com/OrchardLinux/orchard)**: hardware support and the `orchard` tool in one repository (formerly `hardware`, with `cli` merged in as a subtree, its commit IDs unchanged; `cli` is archived). A device change carries its descriptor, firmware recipe, converter steps, kernel patches and tests in one pull request.
- **omarchy-a** and **minimal-linux**: the two maintained distributions.

Inside orchard, code is split by where it runs and how it is used:

- `src/orchard/`: all Python imported on the host, one package.
- `devices/`, `schemas/`, `features.toml`: data.
- `kernel/`, `boot/`: recipes, standalone programs. They run `src/orchard/buildenv.py` by path and need only Python 3.11 and its standard library.
- `runtime/`: device-side code, copied into images, never imported.
- `examples/`: RAM payloads to start from.

There is no wheel: orchard runs from a checkout. One version for the repository (0.2.0); releases publish component bundles, the loaders `m1n1.bin` and `pongo.bin`, sources and checksums.

## Getting started (done)

`git clone`, `./setup`, `orchard run examples/minimal-boot`. `./setup` (and `orchard
setup`) shows every host requirement as ready, todo or yours, asks once, and can be
rerun until everything is ready; `orchard doctor` shows the same and changes
nothing. The requirements are one list shared with the installer's boot tools
(`setup-host`): OpenSSH, libusb, palera1n and Linux USB permissions for booting;
git, Podman (and its machine on macOS), the `orchard` command and PATH for building.
Packages come from glu, then Homebrew on macOS (neither is installed for you), or
apt, dnf or pacman. The `orchard` command links to mainline's launcher, so it
does not depend on any clone. Pongo comes from the latest release while its recipe
is unchanged, so most builds need no Xcode.

## Development: which orchard runs (done)

`bin/orchard` is the `orchard` on PATH. Walking up from the current directory, the first match wins:

1. an orchard checkout (`pyproject.toml` of the project `orchard`): that checkout, e.g. a worktree on your branch;
2. an `orchard.local.toml` with `orchard = "PATH"`: the checkout at PATH;
3. otherwise mainline: orchard's `main`, cloned into `~/.cache/orchard/orchard-main` and fetched before use at most every ten minutes.

The checkout's environment is `<checkout>/.venv`, managed by uv. `orchard python ARGS` runs a distribution's own scripts and tests in it; `orchard checkout` prints which checkout runs.

Parallel work gets one orchard worktree per branch (`git worktree add ../orchard-gpu -b gpu-a10`). Two agents in two checkouts never share an orchard, and nothing pins one.

## Files (done)

| File | Committed | Contains |
|---|---|---|
| `payload.toml` beside a payload's `init` | yes | the payload's own settings: `transport`, `modules`, `firmware`; commented, since the examples teach the system |
| `orchard.local.toml` | never | this checkout's choices: `orchard = "PATH"`, `device = "BOARD"` |

No repository and no per-person setting names a board: many contributors work on several. A payload serves every board of one platform (`out/PLATFORM/`); `orchard run` takes the platform from the attached board, builds what is missing and adds that board's firmware as its own layer from the private cache; `orchard build` takes it from `--platform`, `--device`, `orchard.local.toml` or the attached board (listing USB devices only). A distribution release serves every board: one root image, and per platform a recovery and orchard's platform layer (kernel, DTBs, modules; see [boot contract](boot-contract.md#kernel-dtbs-and-modules)). Which boards are supported, and how well, is only orchard's device data (`status`, `caveats`), which the installer shows.

## Distributions (done)

A distribution has no submodule, no launcher and no root configuration. `orchard` runs mainline, or the orchard checkout its `orchard.local.toml` names. Distribution CI tests against orchard `main`; a release build uses one published orchard release whole, its tools and its per-platform component bundles alike (`ORCHARD_REF` overrides the tools). A release's manifest and build receipt record the exact orchard commit (`orchard.hardware.revision()`).

## Testing an install without a device

`orchard python tools/qemu-install/run RELEASE_DIRECTORY --board BOARD` runs orchard's real
device installer against a 16 GiB loop disk (an APFS-like partition first, as on an
iPad): partitions, root image, the board's platform layer, account and boot entry. Then
QEMU cold-boots that disk as NVMe through orchard's initrd into the system's init. QEMU
cannot run orchard's Apple kernels, so the boot uses Alpine's virt kernel with its storage
modules in orchard's initrd; drivers and the desktop stay for the device. It needs Podman
with a rootful connection (loop devices) and `qemu-system-aarch64`.

## Build caches (done)

All shared by every checkout in `~/.cache/orchard`:

| Layer | |
|---|---|
| Source | shared Git object store (`git/`), one kernel tree per platform and upstream pin (`kernel-work/`) |
| Intermediate | the kernel tree's incremental output; ccache (`ccache/kernel/`); recipe work directories (`work/RECIPE/KEY`) |
| Result | content-addressed components (`components-v2/`), each file stored once; `orchard cache --prune` keeps the most recently used |

Measured on apple-16k (j71b), switching between two patch sets that recompile 109 objects: 120 s without ccache, 83 s with it, 47 s after applying the series in one `git apply`, hashing it in one call and running one `make` job per CPU. The kernel key for a fully cached build went from 16.3 s to 0.8 s. The real cache went from 3.1 GB to 287 MB on disk.

Measuring on the shared tree: hold the tree lock across runs (`ORCHARD_KERNEL_WORK_LOCKED`) and register test series in the shared Git store first, or another session's build switches the tree between runs.

Not used: Docker/BuildKit layers are linear; Nix would rebuild every patch variant of the kernel fully and fits poorly with macOS and Podman; Bazel and Buck2 fight Kbuild.

## Python (done)

Recipes run `$ORCHARD_PYTHON` (default `python3`), which `orchard` sets to its own interpreter. Python 3.11 is the minimum; CI tests 3.11 and 3.14.

## Next: distributions as projects, the device layer and the installer

One step, because all three need a distribution build that does not depend on the
board:

- **Device layer** (done): kernel, initrd, DTBs and modules are orchard's platform
  layer, which the installer writes into the root, and the installer writes the boot
  entry; a distribution builds one root image for every platform, names no board and
  needs no kernel-install. Release format: `schemas/v2`.
- **A distribution as a project:** `distro.toml` names its build entry. In a
  distribution checkout, `orchard build` builds the whole image (root filesystem,
  recovery, release directory with `index.toml`), and `orchard run` builds what is
  missing and starts the recovery from RAM without installing; `orchard install`
  stays separate.
- **The installer and the user's orchard,** below.

## The installer and the user's orchard

Users install today with the released Orchard 0.1.10 through omarchy-a's `install.sh`; that stays until this step.

- **Core:** the part of `src/orchard` that install and launch need (USB, transports, manifests, downloads, install layout, boot disk, firmware steps), standard library only, enforced by an import test.
- **Installer per distribution release:** `install.sh` plus `orchard-install.pyz` (core, the release manifest, the device data and loaders for the release's devices), run by a hash-pinned standalone Python 3.14 the script downloads. No system Python, no uv for users.
- **The persistent user `orchard`:** an install leaves `orchard launch`, `devices` and `update`. An installer replaces it only with a newer core, never an older one. Later a self-contained Rust/C binary.
- **State** in `~/.cache/orchard` gets a version; an older tool refuses newer state. Every tool version shares that cache today.
- **Validation** of manifests and descriptors moves to release CI; jsonschema becomes a development dependency.
- Update omarchy-a's `install.sh` and README at the same time.

## Backlog

- `orchard run` in a distribution boots the locally built system over NFS root (#30) without installing: the desktop's edit-and-run loop.
- Prebuilt kernel layers from CI, first for a newcomer's first build, which otherwise compiles Linux.
- Keep the kernel tree inside the Podman VM (the largest remaining build cost is `make` walking the tree through the host file share).
- A `kernel/build` shortcut when tree, config, image and series match the last build; skip the image check when its inputs match.
- Warn about hand edits in the shared kernel tree; they are built silently and reach every checkout with the same series.
- Prebuilt kernel layers also for upstream pin bumps, which are always full builds.
- Several kernel trees per platform, if checkouts waiting for each other becomes a problem.
- Converter steps next to the device data; IOKit USB on macOS (no libusb); no checkra1n dependency; the iOS app installer with APFS resize.

## Not planned

Checking attestations on the client (releases are attested and `publish.yml` verifies them), Windows hosts, splitting the tablet desktop into upstream-style packages yet.

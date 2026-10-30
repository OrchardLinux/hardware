# Local RAM-boot development

```sh
orchard build examples/minimal-boot
orchard run examples/minimal-boot
```

In an editable distro workspace, use its root `./orchard` launcher to select
local CLI/hardware sources and prepare Python without activating an environment.
The workspace `.venv` can also be selected by an editor for bare `orchard` commands.

## Default device recipe

Put `device = "j71b"` in the repository's `orchard.toml` to select its default
build recipe. RAM builds use the nearest config at or above the experiment,
without crossing a Git repository boundary. `orchard build DIRECTORY --device NAME`
overrides it; without a config the existing `j71b` default remains. No registered
device description is required. The file currently accepts only `device`.
Distro-owned producers can use `orchard.build.configured_device(project_directory)`
for the same default. Installation does not read this build setting and still
rejects incompatible devices.

## Directory content

A build directory contains an executable startup script named `init` and optional
`rootfs/` content. `init` becomes `/init`; files beneath `rootfs/` appear at the same
relative paths inside the RAM filesystem and retain their permissions. No
`build.py`, `run.py`, registration or manifest language is needed. Symlinks in the
CLI's `rootfs/` input are currently rejected, not silently dereferenced.

The CLI supplies BusyBox, the hardware's other static tools (Dropbear, and
`wpa_supplicant`, `wpa_cli` and `iw` for Wi-Fi), the device kernel and boot
framing, then writes artifacts to `DIRECTORY/out/`. Modules are omitted by default;
`--modules` includes matching modules for manual use without automatically loading
them. Firmware is omitted by default; `--firmware` includes every output of the
device's firmware recipe at its destination (`lib/firmware/…`), produced and cached
as the installer does: the first build downloads the device's IPSW. Component override
flags: `--hardware`, `--cache`, `--kernel-dir`, `--busybox`, `--loader`, `--pongo`.
No parent-directory layout is required: `examples/`, `use_cases/`, a deeply nested
folder or an unrelated absolute path all work. Hardware resources come from the
hardware data and recipes of the orchard checkout it runs from,
not a guessed sibling directory. `--hardware PATH` explicitly overrides that source.
The default shared cache is `~/.cache/orchard/`, respecting `XDG_CACHE_HOME`;
`ORCHARD_CACHE_DIR` or `--cache DIR` overrides its root. Outputs stay in the supplied
directory's `out/`, independently of its name or location.

`orchard run DIRECTORY` verifies the last successful payload, shows the sole
connected device, asks `Boot this device? [Y/n]`, and uploads once via Pongo.
A different chip/board or an unknown payload profile produces a plain-language
warning, not a rejection. The prompt then becomes `Try booting anyway? [y/N]`;
Enter cancels, while `y` or `yes` attempts the boot. `--yes` prints the warning
and proceeds without prompting. Upload checks track the connected device's actual
identity, not the payload's expected chip/board. Exactly one device is still required.
It never installs a rootfs, calls a device RPC or claims Linux readiness. It requires
an interactive terminal by default. Use `orchard run DIRECTORY --yes` (or `-y`)
to explicitly authorize a non-interactive boot without the prompt. This skips
only confirmation: payload verification, sole-device/identity checks, operation
locking, refusal to reboot running Linux and the single-attempt policy remain.
A custom `/init` owns device behavior; the host tool cannot infer its safety.

## Lower-level Python functions

The Python functions are separated by responsibility:

- `orchard.build.prepare_kernel(hardware=None, cache=None, device='j71b')`:
  build/reuse a coherent kernel/DTB/module bundle only. Defaults to the active
  hardware package and normal shared cache; does not build loaders, Pongo or tools.
  A distro-owned producer can consume this bundle without any distro setup or
  assembly knowledge in CLI. Uses exactly the same kernel keys and artifacts as
  `prepare_components`, including when called from a fresh hardware checkout.
- `orchard.build.prepare_components(hardware, cache, ...)`: shared component
  building and cache reuse. Invokes hardware-owned kernel/tool/loader producers;
  does not compose RAM roots, install systems or access a device.
- `orchard.ram.assemble_ram(hardware, output, *, kernel, busybox, loader,
  pongo, init, ...)`: RAM-root composition and boot-payload assembly from explicit
  components. Supports `files={destination: source}`, custom boot arguments,
  hostname, device DTB filename and optional modules.
- `orchard.ram.build_directory(directory, ...)`: composes directory content,
  delegating component preparation to `orchard.build`.
- `orchard.boot.boot_ram(output)`: verification, confirmation and tethered
  upload through existing USB/Pongo transport.
- `orchard.boot.run_directory(directory)`: CLI run operation.

Cache and acquisition policy is CLI-owned (`sources.py`, `build.py`). CLI also
owns module-SDK export and reuse (`kernel.py`, `kernel_sdk.py`). Hardware supplies
unchanged kernel/config/patch, module, DTB, staging and toolchain recipes. No new
hardware API, package version or hardware commit is needed for these workflows.
The broader hardware/CLI boundary remains open; example content and build/run
commands do not depend on where those internals eventually settle.

The CLI does not supply a startup script. Callers and examples own `/init`.

There is no required Orchard device agent, installer protocol or device API.

## Cache behavior

The shared cache contains retained upstream Git objects and immutable input-keyed
components. A missing local source tree has the same key as the declared patched
kernel tree. Consequently, a fresh checkout with matching inputs reuses both the
kernel base and completed device layer without invoking a compiler or materializing
Linux. Local tracked/untracked source edits are snapshotted without changing the
user's Git index and select different kernel bases.

Retained kernel bases exclude external modules, final-DTB overlays, firmware
recipes, documentation and unrelated device inputs. CLI exports an SDK with
matching configuration, generated headers, Module.symvers and host build tools.
To avoid extending the hardware API, the initial base fill invokes its existing
full producer, then retains only the kernel/in-tree modules and SDK. That first
fill also builds external modules and overlays as discarded byproducts.

Subsequent module/DTB edits bypass the full producer. CLI restores a private SDK
copy and in-tree module paths, then calls the original hardware module/staging/DTB
recipes. No make command writes into the shared cached SDK.

Each component records the inputs of its key in `key-inputs.json`. On a miss the
build prints which inputs differ from the newest cached entry of that component,
e.g. `differs from newest cached 0bd1204e3f1a in: tree`. The device layer depends
on the device's DTB selection only, not on the rest of `device.toml`.
`orchard cache` lists the shared components with their size, last use and inputs;
`orchard cache kernel-j71b/0bd1204e` removes one entry and `orchard cache kernel-j71b`
all of a component's entries. `orchard cache --prune` keeps the five most recently
used entries of each component (`--keep N` for another number) and removes
unfinished builds.

Entries store each file once: a file whose content and mode another entry already
has becomes a hard link to the single copy in `components-v2/.objects/`. Kernel
entries of one device mostly differ in a few files, so sharing makes about 90% of
their size disappear from disk. The listing shows both the entries' size and what
they use on disk.

Cached artifacts are hash-checked, inputs are rechecked before publication, and a
cache lock makes a second build into the same cache wait for the first. Editable Linux source
and incremental compiler output stay under each hardware checkout's
`kernel/.work/`. On a cache miss, source acquisition borrows upstream objects
from the shared store; it never resets an existing checkout. Do not prune/delete
that store while source trees borrow its objects. Existing sources can seed the
store locally rather than redownloading Linux.

`init` and rootfs files are assembly inputs, not kernel inputs. Toolchain containers
still use the host's configured Podman connection; no VM management is introduced.
When patches change, CLI checks the shared cache against the newly declared
source tree before changing the local checkout. On a miss, it applies the verified
delta from a recorded clean baseline, retaining the index, unchanged source
mtimes and compiler output. Local edits or an unknown baseline stop that transition
without resetting the checkout. Hardware receives the matching patch marker.

Kernel-base keys fingerprint the base producer and SDK exporter, not unrelated
source-management functions. Verified caches from the two earlier equivalent
base producers remain reusable, including the charging-worker cache. A changed
base producer does not inherit that compatibility. Cache reuse still checks the
complete file inventory and hashes.

Developer receipts are not publisher signatures or hardware qualification.
Explicit component paths are trusted inputs: callers must keep kernel, DTB and
modules coherent. Never automatically retry uncertain boot or storage mutations.

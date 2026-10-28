# P105 kernel source preparation

These resources prepare Cascadia's ARM32 kernel and RAM boot environment for
the original iPad mini Wi-Fi (`iPad2,5` / A1432 / `p105ap`). The installed-system
handoff and Home-button patches live in [runtime/p105](../../runtime/p105/).
P105 uses Cascadia's `zImage` and staging loader. `kernel/build p105` produces
public kernel components or an explicitly private boot bundle. The Orchard
CLI adapter consumes an owner-private boot manifest. Public launch assets
remain part of release integration.

## Source pins and scope

[`upstream.lock`](upstream.lock) records:

- Cascadia `c20b85ebb951c704d7e3a664c378dde8276e3928`.
- Linux `adc218676eef25575469234709c2d87185ca223a` (`v6.12`).

[`prepare-source`](prepare-source) exports tracked Cascadia source files,
applies the two runtime patches, [`private-calibration.patch`](private-calibration.patch)
[`native-container.patch`](native-container.patch) and
[`nand-disabled.patch`](nand-disabled.patch),
then merges [`config.fragment`](config.fragment) into `config/p105ap.config`.
It replaces existing definitions of the selected symbols before appending the
fragment, because Cascadia rejects contradictory config entries.

The prepared tree contains `orchard-p105-source.json` with the revisions,
public patch/config hashes and the excluded pathname. The helper performs no
USB operations and produces no boot image. The merged configuration disables
the P105 NAND driver, and the device tree disables the flash-controller node. Cascadia's
reference geometry belongs to another NAND capacity; the ordinary desktop
build excludes that driver and its raw NAND command tools.

## Component producer

Run from this checkout or use the same paths under the installed hardware
package. Python 3.9+, Git and a running Podman engine are required. Choose new
work and output directories outside the hardware package:

```sh
python3 kernel/p105/build \
  --work /path/to/p105-public-work \
  --output /path/to/p105-public-components
```

`kernel/build p105` accepts the same options. The producer fetches both source
pins, exports their tracked source, and compiles in a fresh Linux volume with
network access disabled. Its Containerfile supplies an ARM cross compiler on
a digest-pinned Ubuntu base. Package versions can change when rebuilding the
compiler image; the receipt records its exact image identity and tool output.
An explicit `--toolchain-image IMAGE` can select an already prepared compiler.

The public output contains `zImage`, `p105ap.dtb`, `kernel.config`,
`kernel.release`, compiler/binutils descriptions and `components.json`.
This kernel has an empty embedded initramfs and empty `CONFIG_EXTRA_FIRMWARE`.
It is a component for later composition. A bootable P105 image needs an
embedded owner root because the staging loader does not supply an external
initrd pointer.

After preparing the owner's RAM filesystem through the manual steps below,
build the private image with explicit inputs:

```sh
python3 kernel/p105/build \
  --work /path/to/p105-private-work \
  --output /path/to/p105-private-components \
  --private-initramfs /path/to/owner-initramfs \
  --calibration /path/to/owner-MtCl.bin \
  --nfs-root 10.55.0.1:/path/to/exported/armv7-root
```

Private input validation happens before fetching source or starting a build.
The calibration must be a separate file matching the selected initramfs.
Public firmware hashes must match `devices/p105/firmware.json`. The producer
overlays the packaged boot sources and rebuilds `z2-boot` and `p105-keys`.
It removes the reference NAND utilities from the desktop root. An explicit
initramfs avoids Cascadia's implicit search for host SSH public keys.

The NFS option is private host configuration. It creates `/etc/nfsroot` in
the staged image. Omitting it retains any supplied root setting; an input
without that file boots Cascadia's RAM environment. Prepare the host export
and USB address before the installed-root handoff.

Private output additionally contains `staging-bundle.bin`,
`staging-loader.bin` and `initramfs.tar`. The receipt sets `private: true`
and `publishable: false`; these files include the owner's firmware and access
material. Extract the returned initramfs into a private directory for the
runtime installer so the installed root receives this build's hardware tools.

`--linux-bundle FILE` accepts a local Git object bundle whose HEAD matches the
pinned Linux commit. `--cascadia-source DIRECTORY` accepts a clean checkout at
the Cascadia pin. Both inputs are exported before compilation, so the build
volume has no source-checkout mount. `--jobs N` controls compiler concurrency.
Completed volumes are removed by default. `--keep-work` retains one for
inspection. After a failed build, `state.json` identifies that build's owned
volume; inspect its logs and remove that exact volume when finished.

The producer checks the final config and compiled DTB, validates bundle
framing, and records component hashes before promoting a complete output.
Build success still requires a fresh tethered boot and physical touch check
before updating the device qualification claim.

## Private inputs and outputs

An explicit owner root containing `etc/orchard/p105.json` selects Orchard RAM
recovery. Minimal Linux's P105 composer produces that root from signed ARMv7
Alpine packages and the owner's hardware inputs. The config records its P105
ECID and release. The component receipt adds `mode: orchard-recovery` and
`device_ecid`; keep this receipt with the private bundle.

This mode combines the ordinary kernel fragment with `recovery.fragment`,
preserving the NAND fence and the installed system's prerequisites. It disables
`CONFIG_USB_CDC_COMPOSITE` and uses configfs for the ECM/ACM gadget. Userspace
publishes the selected ECID and live kernel boot ID before SSH bootstrap.
The initramfs waits for an authenticated NFS request through `orchard-device`.

The required USB symbol check in the pinned kernel script selects
`CONFIG_USB_LIBCOMPOSITE` for this mode. The component wrapper then verifies
configfs is enabled and the original composite gadget is disabled. The
ordinary private desktop recipe keeps its existing boot path.

Cascadia's pinned source tracks a reference panel calibration at
`initramfs/lib/firmware/mtcal.bin`. Preparation excludes that pathname before
exporting file data. The reference is never read, hashed or copied by this
recipe. Both `./cascadia build` and the direct kernel script require the
owner's nonempty `build/keep/lib/firmware/mtcal.bin`; the kernel script also
checks that the embedded initramfs contains the same bytes.

Capture the calibration from the owner's iPad, or supply a previous capture
from that same panel. A filename and matching bytes establish consistency;
the operator remains responsible for choosing the owner's capture.

The final kernel embeds its initramfs, including Apple firmware and panel
calibration. An ordinary owner root can also contain SSH access material.
The Orchard recovery composer clears inherited SSH keys and generates its
diagnostic host key in RAM after serial bootstrap. Keep
the prepared/build tree, container volumes, kernel, initramfs and staging
bundles outside this hardware checkout. Treat those build outputs as private.
They belong outside public releases, CI artifacts and source packages.

## Requirements

Source preparation requires Python 3.9+, Git and awk. The manual build below
uses a working Docker-compatible `docker` command and a fresh Linux volume for
the kernel work tree. On macOS the engine must have a running Linux VM. The
source and private inputs are copied into its native filesystem before kernel
compilation; a shared Mac directory is used only for inputs and final copies.

Cascadia's pinned `Dockerfile` provides the cross compiler, binutils, `dtc`,
GNU build tools, Python crypto/disassembly libraries and firmware tools.
Host-side rootfs preparation also uses curl or wget, tar, cpio, gzip and
rsync. Allocate at least 12 GB for a fresh source/build tree and the IPSW.

Qualification used a native Ubuntu 22.04 ARM64 build container with
`arm-linux-gnueabihf-gcc` 11.4.0
(`Ubuntu 11.4.0-1ubuntu1~22.04.3`) and binutils 2.38. ARM32 target binaries
are cross-compiled or unpacked during this kernel/initramfs flow. ARM32
emulation is a separate requirement for building target userspace software.

The source revisions and patches are pinned here. Cascadia's Dockerfile
obtains distro packages and iBoot32Patcher sources at build time, so it does
not lock every compiler/firmware-tool dependency. Retain the container image
digest and tool versions with private build evidence when comparing outputs.

## Prepare source

Run from the hardware checkout. The destination must be new and outside both
this checkout and an optional input checkout.

```sh
P105_WORK="$HOME/p105-private"
python3 kernel/p105/prepare-source --output "$P105_WORK/cascadia"
```

The helper fetches the pinned Cascadia commit into a temporary bare repository.
To use an existing clean checkout at that commit instead:

```sh
python3 kernel/p105/prepare-source \
  --source /path/to/clean/Project-Cascadia \
  --output "$P105_WORK/cascadia"
```

Tracked source files must match the pin. Ignored build inputs from an existing
checkout are excluded from the export. The intentionally excluded reference
calibration is outside the clean-source check.

The kernel is fetched by commit inside the Linux build volume below. This
avoids a tag lookup and keeps its work tree on a Linux filesystem.

## Prepare the owner's RAM environment

Obtain `iPad2,5_8.4.1_12H321_Restore.ipsw` from Apple's firmware service as
described in Cascadia's pinned `docs/QUICKSTART.md`. The IPSW supplies firmware
and boot components; the installed iOS version stays in place.

Create an SSH key for the owner's host and pass its public half explicitly.
Run these commands in the prepared tree, with Docker available:

```sh
cd "$P105_WORK/cascadia"
IPSW=/path/to/owner-inputs/iPad2,5_8.4.1_12H321_Restore.ipsw ./cascadia firmware
install -d build
install -m 0644 /path/to/owner-inputs/host-key.pub build/host-public-key.pub
PUBKEY="$PWD/build/host-public-key.pub" ./cascadia rootfs
```

For a jailbroken iPad with OpenSSH, Legacy iOS Kit and a Lightning data cable,
capture its calibration while iOS is running:

```sh
LIK=/path/to/Legacy-iOS-Kit ./cascadia mtcal
```

This writes `build/keep/lib/firmware/mtcal.bin`. To use an existing owner
capture, place it at that private path before building:

```sh
install -d build/keep/lib/firmware
install -m 0600 /path/to/owner-inputs/MtCl.bin build/keep/lib/firmware/mtcal.bin
```

For a RAM-only build, continue to the build section. An installed NFS system also
needs the marker and runtime described in
[`runtime/p105/README.md`](../../runtime/p105/README.md), an assembled userspace,
and an export prepared by its owner. Set the owner's NFS location in
`build/keep/etc/nfsroot` before the final build. That file is private host
configuration and is embedded in the resulting kernel.

## Build and verify

Choose a fresh volume name. Cascadia's preceding firmware/rootfs commands build
the standard compiler image from its pinned Dockerfile. The small native
container adapter lets `./cascadia build` use that image's tools directly,
without trying to start another Docker engine inside the container. Set
`CASCADIA_BUILD_IN_CONTAINER=1` only in this compiler environment.

```sh
P105_VOLUME=p105-private-build
mkdir -p "$P105_WORK/artifacts"
chmod 700 "$P105_WORK/artifacts"
docker volume create "$P105_VOLUME"
docker run --rm \
  -v "$PWD:/input:ro" \
  -v "$P105_VOLUME:/cascadia" \
  -v "$P105_WORK/artifacts:/artifacts" \
  -e CASCADIA_BUILD_IN_CONTAINER=1 \
  -e PUBKEY=/cascadia/build/host-public-key.pub \
  -w /cascadia cascadia-build bash -euc '
    test -z "$(ls -A /cascadia)" || { echo "Choose a fresh build volume" >&2; exit 1; }
    rsync -a --exclude /build/linux/ --exclude /output/ --exclude /logs/ /input/ /cascadia/
    chown -R 0:0 build/initramfs-root build/keep
    mkdir -p build/linux
    git -C build/linux init
    git -C build/linux remote add origin https://github.com/torvalds/linux.git
    git -C build/linux fetch --depth 1 origin adc218676eef25575469234709c2d87185ca223a
    git -C build/linux checkout --detach FETCH_HEAD
    test "$(git -C build/linux rev-parse HEAD)" = adc218676eef25575469234709c2d87185ca223a
    ./cascadia build
    for symbol in CONFIG_NAMESPACES CONFIG_USER_NS CONFIG_CGROUPS CONFIG_MEMCG \
      CONFIG_CGROUP_PIDS CONFIG_POSIX_MQUEUE CONFIG_ZRAM CONFIG_ZRAM_BACKEND_LZ4 \
      CONFIG_FUSE_FS; do
      grep -q "^${symbol}=y$" build/linux/.config || exit 1
    done
    grep -q "^CONFIG_ZRAM_DEF_COMP=\"lz4\"$" build/linux/.config
    cp -a output/. /artifacts/
    cp build/out/kernel-config /artifacts/kernel-config
    cp orchard-p105-source.json /artifacts/
  '
```

Cascadia applies its Linux patches, checks critical kernel symbols and boot
stamps, embeds the current RAM filesystem, then produces
`output/zImage-dtb`, `output/staging-bundle.bin` and
`output/staging-loader.bin`. These are copied to the private `artifacts`
directory above; the work tree and logs remain in the private Linux volume.
Keep that volume and `build/out/kernel-config` with the owner's private build
evidence. The build's initramfs validation checks
its boot scripts, ACM console and pinned USB address.

Use the completed volume's `/cascadia/build/initramfs-root` as the runtime
installer input, so it receives this build's static tools and Home-button
change. The host's initial RAM tree was an input to that compilation.

Copy the two private boot payloads into the prepared host tree before using
Cascadia's host-side boot command:

```sh
install -d output
install -m 0600 "$P105_WORK/artifacts/staging-bundle.bin" output/staging-bundle.bin
install -m 0600 "$P105_WORK/artifacts/staging-loader.bin" output/staging-loader.bin
```

Use Cascadia's manual boot instructions with the owner's existing jailbreak
or supported A5 exploit hardware. A jailbreak-based boot uses the plaintext
iBEC selected by `./cascadia flash --kdfu`. Cold restarts require the host boot
flow again. Hardware qualification includes another cold boot and the
installed runtime checks; source preparation alone qualifies neither.

The manual host transport remains necessary until the P105 Orchard adapter
is implemented. These components have their own build receipt; published
Orchard release manifests and the authenticated device endpoint are later
contributions.

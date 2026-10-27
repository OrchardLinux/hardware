# P105 kernel source preparation

These resources prepare Cascadia's ARM32 kernel and RAM boot environment for
the original iPad mini Wi-Fi (`iPad2,5` / A1432 / `p105ap`). The installed-system
handoff and Home-button patches live in [runtime/p105](../../runtime/p105/).
P105 uses Cascadia's `zImage` and staging loader. Integration with the shared
`kernel/build` producer and Orchard's published launch format remains future
work; use the manual flow below.

## Source pins and scope

[`upstream.lock`](upstream.lock) records:

- Cascadia `c20b85ebb951c704d7e3a664c378dde8276e3928`.
- Linux `adc218676eef25575469234709c2d87185ca223a` (`v6.12`).

[`prepare-source`](prepare-source) exports tracked Cascadia source files,
applies the two runtime patches, [`private-calibration.patch`](private-calibration.patch)
and [`native-container.patch`](native-container.patch),
then merges [`config.fragment`](config.fragment) into `config/p105ap.config`.
It replaces existing definitions of the selected symbols before appending the
fragment, because Cascadia rejects contradictory config entries.

The prepared tree contains `orchard-p105-source.json` with the revisions,
public patch/config hashes and the excluded pathname. The helper performs no
USB operations and produces no boot image.

## Private inputs and outputs

Cascadia's pinned source tracks a reference panel calibration at
`initramfs/lib/firmware/mtcal.bin`. Preparation excludes that pathname before
exporting file data. The reference is never read, hashed or copied by this
recipe. Both `./cascadia build` and the direct kernel script require the
owner's nonempty `build/keep/lib/firmware/mtcal.bin`; the kernel script also
checks that the embedded initramfs contains the same bytes.

Capture the calibration from the owner's iPad, or supply a previous capture
from that same panel. A filename and matching bytes establish consistency;
the operator remains responsible for choosing the owner's capture.

The final kernel embeds its initramfs. That includes Apple firmware, panel
calibration and SSH access material, including the Linux SSH host key. Keep
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

P105 currently uses the manual Cascadia adapter. It has no published Orchard
release manifest, authenticated Orchard device endpoint or generic-producer
output contract in this change.

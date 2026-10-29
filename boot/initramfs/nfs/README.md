# NFS RAM startup

This shared hardware runtime starts a systemd root over a dedicated USB link.
It owns configfs USB, the network link, root validation and the PID 1 handoff.
The distro prepares the root filesystem and decides which desktop to start.
Host software supplies the interface and NFS export. This path performs no
internal-storage operation.

The first tested consumer is experimental N61, which requires N61 hardware
support to be installed. The kernel must include ECM or NCM, ACM, configfs,
the device's USB PHY/controller and NFSv4.0 as built-ins. Configfs must be
able to bind the USB device controller. The root must carry the matching
`/usr/lib/modules/<release>` tree for other drivers. Starting a desktop needs
a fresh RAM boot: the original N61 diagnostic `/init` has a foreground shell
loop without a PID 1 handoff hook.

Earlier development builds completed the PID 1 handoff over ECM to an Arch
NFS root on N61. This remains a bring-up runtime and has not been qualified
for installation or other device profiles.

## Root identity and staging

Prepare the aarch64 distro root on a filesystem that preserves Linux file
names, numeric ownership and modes. Copy the exact built kernel module tree
into `/usr/lib/modules/<kernel_release>`. Leave `/dev`, `/proc`, `/sys` and
`/run` as real directories. The root's regular, root-owned
`/etc/orchard/nfs-root.json` contains exactly:

```json
{
  "schema": 1,
  "kind": "orchard.nfs-root",
  "profile": "n61",
  "architecture": "aarch64",
  "kernel_release": "7.3.0-rc1-orchard-apple-4k",
  "root_id": "06106106-1061-4061-8061-061061061061",
  "image_sha256": "<64 lowercase hex digits from the public image archive>",
  "image_receipt_sha256": "<64 lowercase hex digits from its public receipt>"
}
```

Mint a fresh canonical UUID for each private root. The image digests record
the distro inputs; they do not authenticate the exported filesystem. The RAM
builder embeds the SHA-256 of these exact JSON bytes, so host and runtime agree
without a JSON interpreter in RAM.
The running kernel release and device-tree compatible must also match. The
runtime inspects the systemd ELF header before executing the new root.
The trusted host controls the writable export after this check.

```sh
python -m orchard_hardware.boot.initramfs.nfs.stage \
  --receipt /path/to/root/etc/orchard/nfs-root.json \
  --busybox /path/to/static-aarch64-busybox \
  --output /path/to/new-ram-root \
  --server 10.61.0.1 --address 10.61.0.2/30 \
  --export /n61-root --port 32061 --function ecm --handoff manual \
  > /path/to/nfs-runtime-receipt.json
```

The output uses native `orchard build DIRECTORY` layout, with `init`,
`orchard.toml` and a `rootfs` overlay. Pass the same `--busybox` file to that
build, together with the existing N61 kernel, m1n1 and qualified Pongo inputs.
Keep the resulting RAM image and staging
receipt private because they select a specific writable root. The builder
requires a fresh output path and a static AArch64 BusyBox. ECM is the default;
NCM can be selected after the host's driver has been qualified. The generated
MAC addresses derive from the two IPv4 addresses and appear in the runtime
receipt.

Use the pinned hardware BusyBox recipe, which supplies `awk`, `mount`, `stat`,
`sha256sum`, `od`, `timeout`, `setsid` and `switch_root`. Mount tracking reads
`/proc/self/mountinfo` with `awk`, including while `/proc` lives under
`/newroot`; the pinned BusyBox omits the `mountpoint` applet. Failure recovery
tracks the retired rescue process explicitly so its replacement starts even
while the old PID still exists.

The N61 link reserves host `10.61.0.1`, device `10.61.0.2/30` and NFS TCP port
`32061`. A dedicated userspace NFS server can expose a Linux volume at the
NFSv4 pseudo path `/n61-root`, with `sec=sys`, writable access, numeric IDs and
root ownership preserved. The runtime explicitly requests NFSv4.0 over TCP,
so rpcbind and mountd ports are outside its protocol. Choose an isolated
subnet, port and export that do not collide with existing host services.
Before arming handoff, verify through the NFS client that the root metadata
and systemd retain UID/GID `0:0`; NFSv4 owner mapping must preserve those IDs.

## Handoff and rescue

The default `--handoff manual` leaves the physical ACM shell running after
configuring USB. Create `/run/orchard/nfs-handoff` through that shell when the
host interface and export are ready. `--handoff auto` arms that request during
startup. PID 1 attempts the mount once with a 60-second initial timeout and
refuses a mismatched root. Network failure can still stall later I/O to a hard
NFS mount; the physical rescue tools remain in RAM.

After validation, PID 1 moves `/dev`, `/proc`, `/sys` and `/run` into the new
root and calls BusyBox `switch_root`. The new PID 1 starts systemd. A transient
`orchard-acm-rescue.service` under `/run/systemd/system` runs the copied static
BusyBox and shell from `/run/orchard/rescue`. Its physical USB root access has
the same trusted-host scope as the RAM experiment. A runtime NetworkManager
configuration keeps `usb0` unmanaged so desktop services preserve the NFS
transport. Configfs keeps ownership of the existing composite gadget.

The experimental USB PID is `05ac:4144`. Its descriptor publishes `PROFILE`,
the selected `ROOT` UUID and the live kernel `BOOT` UUID. This minimal runtime
does not read ECID from the ADT; it does not qualify as the installed recovery
protocol. The boot operator must retain the verified target's physical USB
location when selecting its ACM port.

Before this path can present itself as normal Orchard recovery, adapt the
protected identity flow for N61: read and validate its live ADT, publish the
ECID and current boot UUID through the verified serial connection, and retain
that identity across the NFS handoff. Provision the host's public key through
that connection and pin the boot-scoped SSH host key received there. Recheck
the identity after handoff and exercise access retirement and failure recovery.
Until those checks pass, this remains a bring-up runtime.

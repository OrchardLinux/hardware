# iPhone 6 RAM boot

The `n61` profile describes the iPhone 6, `iPhone7,2` / N61AP, with an Apple
A8 T7000. Its DFU identity is chip `0x7000`, board `0x06`. The kernel platform
is `apple-4k`, selecting the upstream `t7000-n61` DTB and 4 KiB ARM64 pages.

Build the public kernel components from this repository:

```sh
./kernel/build n61 --output out/kernel/n61
```

The platform references Orchard's shared patch layers. Kernel output contains
`Image.gz`, `n61.dtb`, `kernel.release` and the module tree. Use the
complete output from one build. Installation is disabled.

The touch recipe names iOS 12.5.1 build 16H22 and its N61 multitouch input.
The Gen1 firmware conversion requires the companion CLI changes. A
loader patch forwards the handset's touch and proximity calibration without
putting calibration bytes in the public recipe. See
[N61 touch](bring-up/n61-touch.md) for the startup sequence and test record.

In an Orchard distro workspace with the paired CLI and hardware sources,
prepare the existing minimal experiment:

```sh
./orchard build examples/minimal-boot --device n61 \
  --pongo /path/to/Pongo-2.6.3-e98323f8.bin
./orchard run examples/minimal-boot
```

The Pongo image used for the recorded N61 boots is the copy bundled with
palera1n 2.4, SHA-256
`5475c5f701423858b34e92176c966a9d4b12950f38acb8d1c347f14d5b272655`.
Enter a fresh DFU session with the iPhone 6. The shared CLI requires one target
and tracks its chip, board, ECID and USB location through Pongo. It warns if
the connected device differs from the build's profile. `--yes` proceeds after
that warning, so verify the selected device before using it. The sample
`init` exposes a USB serial shell from RAM. Record the serial banner and page
size, then inspect CPU and storage inventories. Keep internal storage inactive
during qualification. The RAM shell grants root access to its physical USB
host.

## Minimal-boot qualification

The descriptor's working features come from the RAM experiment. On 2026-10-29,
hardware `f714359636b04b21448bb3aa7f38e86fe27651e5` and CLI
`2e11638b0a1921c6c6ce5bb99d4c96209932f911` produced a `minimal-boot` image
with the recipe-matched private touch firmware and the complete module tree.
Its payload SHA-256 was
`c38daf6b601e00c01a4e1c21c7b1a3ae5927e405a57ce72a721e86729df1b1ad`.
The physical USB shell reported 4 KiB pages, CPUs `0-1` and a 750 by 1334
framebuffer. The owner saw the Linux console on the phone.

After `modprobe apple_z2`, physical taps and swipes produced 1,496 evdev
events, including 9 contact starts and 10 releases during the capture window,
at x=3..747 and y=15..1333. These observations support the display and touch
flags. USB ACM carries the shell; the descriptor leaves USB networking
disabled pending a separate test with this hardware revision. Bluetooth,
Wi-Fi and internal storage remain unqualified. The PR records the exact
producer revision and artifact hashes for each later physical test.

For a touch test, copy `examples/minimal-boot`, stage the privately extracted
firmware at `rootfs/lib/firmware/apple/n61-fwstart.z2fw`, and build with the
matching kernel, loader and complete module tree:

```sh
python -m orchard_cli build <minimal-boot-copy> --hardware <hardware-checkout> \
  --device n61 --cache <cache> --kernel-dir <kernel-output> \
  --loader <m1n1.bin> --pongo <Pongo.bin> --busybox <busybox> --modules
python -m orchard_cli run <minimal-boot-copy> --yes
```

From the physical serial shell, record the boot UUID, `uname -r`,
`KernelPageSize` from `/proc/self/smaps`, `/sys/devices/system/cpu/online`,
and `/sys/class/graphics/fb0/virtual_size`. Load `apple_z2`, find the
`iPhone 6 Touchscreen` event device in `/proc/bus/input/devices`, then capture
that device while tapping and swiping. Retain the raw input events and
`dmesg` with the build hashes. Internal storage stays inactive during this test.

## Earlier NFS desktop tests

On 2026-10-29, the reconciled kernel and private NFS desktop reached the Arch
and Omarchy desktop on two tethered boots. The owner confirmed touch on both;
the first captured y=26..1322 on the 0..1333 axis and entered `n61` through
the on-screen keyboard. Systemd reported zero failed units on both boots.
The tested producer revisions were hardware `853659d` and CLI `8fed88e`.
The RAM payload SHA-256 was
`b2d49d60b3771947e11efcee4d1d7f91d126a333d7373bae02b4d1f341f6ec78`;
the desktop archive SHA-256 was
`a6e0626dc02d8aff535d6c0fa8f14cb54954700bfda5f267528f07cec3d6fa57`.
Those results apply to that combination. Bluetooth, Wi-Fi and internal storage
remain unqualified. Record later physical results with their exact inputs.

## Internal storage gate

The retained N61 ADT, checked against the saved device identity, describes
`/arm-io/ans` as `iop,s5l8960x`. Hoolock's generic storage selector uses its
ANS1 backend for that compatible. The backend has not been exercised on this
phone. Its current shutdown path frees the command buffer twice, and several
initialization failures leave the ANS power and allocation cleanup incomplete.
The read command also needs explicit bounds for its narrower LBA field and
verified device capacity. Repair and test these paths before any read or
shutdown experiment.

Future installation follows Orchard's [boot contract](boot-contract.md): a
FAT32 XBOOTLDR partition holds BLS entries, and roots use PARTUUIDs. The CLI and
installer do not yet implement that contract. One inspected N61 on iOS 12.5.1
had a single APFS container with separate mounted system, data and baseband
volumes. Inspect each device's layout before changing partitions. An iOS 12
resize backend also requires separate qualification.

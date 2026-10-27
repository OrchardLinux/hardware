# Kernel build

`build` fetches the pinned Linux revision, applies the selected target's patches
and config, compiles the kernel and external modules, stages the modules, and
runs the target's final-DTB recipe. Git and Podman are required.

```sh
./kernel/build j71b --output out/kernel/j71b
```

Scratch remains in ignored `kernel/.work/j71b/`. `--resume` continues an
interrupted build using that scratch directory; `--output` must name a new
directory. The result contains `Image.gz`, `j71b.dtb`, `kernel.release` and
`lib/modules/`.

The J71b NVMe module has mutually exclusive load modes: `j71b_ro_test=1`
retains the command-level read-only whitelist; `j71b_rw=1` opts into normal
read-write commands. The separate `nvme-core` module also requires `j71b_rw=1`
to stop forcing the J71b namespace read-only. Both matching modules must be
loaded in a fresh recovery boot. The combined mode is compiled, not yet
hardware-proven.

## J71t

```sh
./kernel/build j71t --output out/kernel/j71t
```

`patches/j71t/` is one plain diff per feature (S8003 PCIe/ANS NVMe, Wi-Fi and
Bluetooth, SmartIO/SPI, N1 touch, audio, power, USB, sensors/backlight, device
trees). It is exported from the linux-a9 `a9/release` integration kernel
([brkosebay/linux](https://github.com/brkosebay/linux), based on the same
`hoolock` revision) by linux-a9's `scripts/orchard/j71t-series.py`.
It leaves out the GPU and video-decoder research code, SEP, AOP and
diagnostics-only drivers. Each patch header names the source commit; the series
applies in order to the pinned revision and reproduces the exported tree.

The J71t NVMe driver is built in and read-only by default. Only NVMe Write
commands inside the boot argument `nvme.write_window=NSID:FIRST-LAST` reach the
controller, never trim/Write Zeroes/Format/Sanitize/firmware/namespace
commands, and namespaces 2-7 stay read-only whatever the window says.
`CONFIG_NVME_APPLE_ANS_EARLY_USB` holds the PCIe host and ANS until recovery
releases them (`runtime/j71t/phase2-hardware`). See
[the J71t recovery README](../boot/initramfs/j71t/recovery/README.md) for how
Orchard's target selection maps onto the window.

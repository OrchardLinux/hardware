# Kernel build

`build` fetches the pinned torvalds revision, applies the platform's ordered
patch layers (`series/<platform>`) and config, and compiles one kernel, its
modules and every Apple DTB. A device name resolves to its platform through
`devices/<device>/device.toml` `[kernel]` and additionally copies its DTB to
`DIRECTORY/<device>.dtb`. Git and Podman are required.

```sh
./kernel/build j71b --output out/kernel/j71b     # platform "apple" + j71b.dtb
./kernel/build apple --output out/kernel/apple   # platform only
```

Scratch remains in ignored `kernel/.work/<platform>/`. `--resume` continues an
interrupted build using that scratch directory; `--output` must name a new
directory. The result contains `Image.gz`, `j71b.dtb`, `kernel.release` and
`lib/modules/`.

The J71b NVMe module has mutually exclusive load modes: `j71b_ro_test=1`
retains the command-level read-only whitelist; `j71b_rw=1` opts into normal
read-write commands. The separate `nvme-core` module also requires `j71b_rw=1`
to stop forcing the J71b namespace read-only. Both matching modules must be
loaded in a fresh recovery boot. The combined mode is compiled, not yet
hardware-proven.

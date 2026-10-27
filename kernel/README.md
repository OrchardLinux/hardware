# Kernel build

P105 has an ARMv7 [component producer](p105/README.md) with separate public
kernel and private embedded-root outputs. Use `kernel/build p105 --work
NEW_WORK --output NEW_OUTPUT`; its desktop build disables the reference NAND
controller. The ARM64 J71b producer is described below.

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

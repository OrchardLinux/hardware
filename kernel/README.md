# Kernel build

`build` fetches the pinned torvalds revision, applies the platform's ordered
patch layers (`series/<platform>`) and config, and compiles one kernel, its
modules and every Apple DTB. A device name resolves to its platform through
`devices/<device>/device.toml` `[kernel]` and additionally copies its DTB to
`DIRECTORY/<device>.dtb`. Git and Podman are required.

```sh
./kernel/build j71b --output out/kernel/j71b     # platform "apple-16k" + j71b.dtb
./kernel/build apple --output out/kernel/apple   # platform only
```

Scratch remains in ignored `kernel/.work/<platform>/`. `--resume` continues an
interrupted build using that scratch directory; `--output` must name a new
directory. The result contains `Image.gz`, `j71b.dtb`, `kernel.release` and
`lib/modules/`.

The kernel has no device-specific NVMe fences. Protecting Apple's data is
the installer's job: the recovery runtime keeps every NVMe disk and
partition read-only and opens only its verified target while writing
(`runtime/j71b/storage.py`).

# Kernel build

`build` fetches the pinned torvalds revision, applies the platform's ordered
patch layers (`series/<platform>`) and config, and compiles one kernel, its
modules and every Apple DTB. A device name resolves to its platform through
`devices/<device>/device.toml` `[kernel]` and additionally copies its DTB to
`DIRECTORY/<device>.dtb`. Git and Podman are required.

```sh
./kernel/build j71b --output out/kernel/j71b     # platform "apple-16k" + j71b.dtb
./kernel/build apple-16k --output out/kernel/apple-16k   # platform only
```

The source and compiler output live in one incremental tree per platform and
upstream pin, shared by every hardware checkout and worktree:
`${ORCHARD_CACHE_DIR:-${XDG_CACHE_HOME:-~/.cache}/orchard}/kernel-work/<platform>-<pin>/`
(`--work-dir` chooses another). Every build continues that tree: a changed patch
series resets the files the old series touched and applies the new one, so only
affected files recompile. Hand edits in the tree are refused rather than lost.
A second build of the same tree waits for the first. `--output` must name a new
directory; it receives `Image.gz`, `<device>.dtb`, `dtbs/`, `kernel.release` and
`lib/modules/`.

For N61, `receipt.json` binds the output files to the platform, source trees,
hardware recipe, patches and configuration. The device-specific DTB must
match its copy under `dtbs/`. Consumers can verify the image, selected DTB and
complete module tree against this receipt.

The kernel has no device-specific NVMe fences. Protecting Apple's data is
the installer's job: the recovery runtime keeps every NVMe disk and
partition read-only and opens only its verified target while writing
(`runtime/j71b/storage.py`).

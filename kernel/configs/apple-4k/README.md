# Kernel configuration for A7, A8 and A8X

The following paragraph describes the initial 4 KiB baseline before the N61
desktop settings listed below.

`config` is the `apple-16k` configuration with 4 KiB pages instead of 16 KiB.
Apple's A7 (S5L8960X), A8 (T7000) and A8X (T7001) CPUs have no 16 KiB
translation granule, so their devices cannot run the `apple-16k` kernel. The
patch layers are the same (`../../series/apple-4k`); only the page size and
what Kconfig derives from it differ (page shift, contiguous-PTE and
max-order sizes, mmap randomization bits, THP swap).

For the N61 desktop, the `apple-4k` config also enables the NFS client, FUSE
and cgroup settings.

## How the file is generated

From `../apple-16k/config`, with `CONFIG_ARM64_4K_PAGES=y` and
`CONFIG_ARM64_16K_PAGES` unset, resolved with `olddefconfig` on the pinned
source as described in `../apple-16k/README.md`. Keep the two files in step: a
change to `apple-16k` is made here too, then resolved again.

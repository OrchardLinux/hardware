# J71t device tree

The J71t kernel patches add the complete S8003 and J71t device trees, so the
kernel build's `s8003-j71t.dtb` is used as is. The adjacent `build` recipe
checks its pinned SHA-256 and writes it to the explicit `--output` path.
`kernel/build j71t --output DIRECTORY` invokes this recipe and includes the
result as `DIRECTORY/j71t.dtb`.

m1n1 fills in memory, the framebuffer, the NVMe host-memory-buffer region
(`nvme-hmb`), the Wi-Fi MAC and Wi-Fi calibration from the live ADT at boot
(`../../../boot/m1n1/patches/j71t/`). The DTB itself carries no per-device data.

The tree still describes blocks this port does not drive (GPU, video decoder,
SEP, AOP); their drivers are not part of the J71t patches.

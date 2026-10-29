# N61 development overlay

This explicitly loaded module lets a running N61 4 KiB kernel test its touch
device tree while preserving the USB desktop and RAM rescue session. It refuses
other boards, a different page size, and an existing SPI2 node. It has no autoload
alias. Native board support belongs in the N61 device tree.

Build with the matching kernel source and a private copy of its module SDK,
using the shared `kernel/build-modules` recipe. Prepare one private, reviewed
DTBO at `/usr/lib/firmware/apple/n61-touch-development.dtbo`; it may contain
the connected phone's calibration. Check its digest and the phone's board and
boot identities before loading the module. Keep that file outside public
images and source control.

The module applies one bounded FDT overlay through the kernel's normal overlay
API. Unloading removes its own overlay. A failed application or removal needs
inspection before further hardware changes. Preserve the existing boot image
and source receipts throughout development.

For a powered test, the helper checks the newly added D2186 touch supply child
and attaches it beneath the existing I2C PMIC before retrying its probe. The
kernel's dynamic OF platform notifier otherwise gives this late child a
platform parent without the PMIC regmap. The helper accepts only its own exact
N61 child path and compatible, and refuses to move an already bound child.
It leaves existing PMIC children in place. Normal native DT population creates
the touch supply beneath the I2C PMIC during boot.

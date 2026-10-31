# Working J72 development code

This inactive development snapshot accompanies draft PR #43. It is not included
in Orchard's patch series or builds and does not register J72 support.

These are the actual sources used in the measured Linux 7.0.12 J72 RAM
experiment, plus build/test helpers. Kernel and diagnostic implementations
are preserved from the working lab copies. They are development code with
exact-board guards and direct hardware access. Review them before reuse.

| Directory | Implementation | Measured evidence |
| --- | --- | --- |
| [spi](spi/) | Complete tested `spi-apple.c`, preserved pre-correction source and its exact patch | Correct wire order and explicit payload delivery; firmware ACK `0x4bc1` |
| [touch](touch/) | Full reusable touch worker, bounded contact decoder, tests and a private-include renderer | One/two-finger native contacts, evdev mapping, menus, Foot and clean hardware restoration |
| [battery](battery/) | BQ27545 HDQ/UART frontend, two-stop-bit serdev API patch, Kconfig/Makefile patch, capacity-pairing patch and tests | Identity `0x0545`, Linux voltage/current/temperature/percentage and consistent remaining/full pairing |
| [charging](charging/) | Read-only PMIC observer and the tested 425 mA one-shot kernel worker/policy core | -764 mA baseline, -423…-400 mA trial, automatic restoration and -767 mA afterward |

The 850 mA follow-up is not included as a measured result: it has not run.
The Foot patch's independent contribution was not isolated from the other
keyboard/input fixes. See [the evidence report](../../docs/bring-up/j72.md).
Desktop sources are published [in the companion repository](https://github.com/thisiscamk/ipad-air-j72-linux/tree/090c68e/code/desktop); they belong in a separate omarchy-a contribution.

## Run tests without an iPad

Requirements: Python 3 and a C compiler supporting UBSan.

```sh
python3 experiments/j72/run-offline-tests.py
```

This uses synthetic input, compiles the real HDQ functions into the harness,
and tests contact bounds, echo alignment, the volatile-write whitelist,
rollback failures and private include generation. It does not contact an
iPad or load a kernel module. The original 1,801-frame contact capture stays
private and is not needed by the public synthetic tests.

## Touch firmware and calibration stay private

The worker includes four generated files containing model firmware and
unit calibration. Those files, compiled modules and boot images are excluded
from this repository. Supply your own already-validated, already-framed J72
packets; the helper neither extracts firmware nor constructs or authenticates
those packets. Matching lengths alone do not prove a packet is correct.

```sh
python3 experiments/j72/touch/make-private-includes.py \
  --firmware /your/private/firmware-packet \
  --proximity /your/private/proximity-packet \
  --panel-first /your/private/panel-packet-0 \
  --panel-last /your/private/panel-packet-1 \
  --output experiments/j72/touch/private/include
```

The output directory is mode 0700, files are 0600, existing files are not
overwritten, and `.inc` files are git-ignored. Keep any resulting module
private too: it embeds those inputs.

With the matching prepared J72 kernel source and output trees, the touch
module can be built with the supplied Makefile:

```sh
make -C "$J72_KERNEL_SRC" O="$J72_KERNEL_OUT" ARCH=arm64 \
  M="$PWD/experiments/j72/touch" modules
```

The variables refer to your configured target-kernel trees, not the host's
running kernel. This is a build example, not an instruction to load the
module. The required controller/clock/PMIC device tree is not bundled; raw
captured Apple device trees must remain private.

## SPI and battery patch bases

`spi/s5l-explicit-transfers.patch` applies to the exact source preserved as
`spi/base/spi-apple.c`, producing `spi/spi-apple.c`. That baseline already
contains the earlier S5L controller work. It is not current Orchard's SPI
driver. The full result is included so the changes can be reviewed without
reconstructing our local kernel tree.

`battery/serdev-stopbits.patch` adds the API needed by the UART frontend.
`battery/build-integration.patch` adds its build entries; place the frontend
at `drivers/power/supply/bq27xxx_battery_hdq_uart.c`. The experimental board
uses a battery child under UART5 with provisional compatible
`ti,bq27540-hdq-uart`; the runtime query requires the measured BQ27545.
`j72-capacity-pairing.patch` is the measured board-specific experiment.
The common driver's machine check must become proper data-driven behavior
before merge into Orchard. Read the source's guards and write restrictions.

Do not open a raw HDQ tty while the kernel frontend owns that UART.

## Build and publication checks

The five kernel sources were recompiled with native ARM64 `W=1` against the
measured #33 headers: SPI, touch, battery, read-only observer and one-shot
charger worker. The local copied `Module.symvers` omitted the stop-bit API;
its actual export was verified in #33's `vmlinux.symvers` and `System.map`
and supplied to the compile check. The kernel Image was not changed.
No rebuilt module was loaded for these publication checks.

This source release does not reproduce the entire boot image or desktop
filesystem. An Orchard implementation still needs the bindings, normal
clock/regulator ownership, firmware recipe and host/runtime integration
listed in [UPSTREAM-PLAN.md](UPSTREAM-PLAN.md), followed by exact-revision
hardware testing. Keep PR #43 in draft while that work is prepared.

#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-only
set -euo pipefail
umask 077
mode=$1
jobs=$2
[[ "$mode" == public || "$mode" == private ]]
[[ "$jobs" =~ ^[1-9][0-9]*$ ]]
test -z "$(ls -A /cascadia)"
tar -xf /inputs/source.tar -C /cascadia
mkdir -p build/linux build/out output logs
tar -xf /inputs/linux.tar -C build/linux
export ARCH=arm CROSS_COMPILE=arm-linux-gnueabihf-
export KBUILD_BUILD_USER=orchard KBUILD_BUILD_HOST=builder KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='2026-10-27T00:00:00Z' LOCALVERSION=''
if [[ "$mode" == private ]]; then
  mkdir -p build/initramfs-root build/keep/lib/firmware
  tar -xf /inputs/initramfs.tar -C build/initramfs-root
  cp /inputs/calibration build/keep/lib/firmware/mtcal.bin
  for tool in z2-boot p105-keys; do
    arm-linux-gnueabihf-gcc -static -Os -Wall -o "build/initramfs-root/sbin/$tool" "tools/$tool.c"
  done
  bash scripts/build-kernel.sh -j "$jobs"
else
  # Public mode has no private input mount and embeds an empty initramfs.
  export ORCHARD_P105_PUBLIC_KERNEL=1 INITRAMFS_DIR=/cascadia/build/no-initramfs
  printf '\nCONFIG_INITRAMFS_SOURCE=""\n' >> config/p105ap.config
  bash scripts/build-kernel.sh -j "$jobs"
fi
config=build/linux/.config
for symbol in CONFIG_NAMESPACES CONFIG_USER_NS CONFIG_CGROUPS CONFIG_MEMCG \
    CONFIG_CGROUP_PIDS CONFIG_POSIX_MQUEUE CONFIG_ZRAM CONFIG_ZRAM_BACKEND_LZ4 CONFIG_FUSE_FS; do
  grep -qx "$symbol=y" "$config"
done
grep -qx 'CONFIG_ZRAM_DEF_COMP="lz4"' "$config"
if grep -Eq '^CONFIG_MTD_APPLE_H2FMI=[ym]$' "$config"; then
  echo 'P105 desktop build must exclude the NAND controller' >&2
  exit 1
fi
grep -qx 'CONFIG_EXTRA_FIRMWARE=""' "$config"
if [[ "$mode" == private && -f build/initramfs-root/etc/orchard/p105.json ]]; then
  grep -qx 'CONFIG_USB_CONFIGFS=y' "$config"
  grep -qx '# CONFIG_USB_CDC_COMPOSITE is not set' "$config"
fi
dtc -I dts -O dtb dts/p105ap.dts -o output/p105ap.dtb 2>logs/dtc.log
test "$(fdtget output/p105ap.dtb /soc@30000000/flash-controller@1200000 status)" = disabled
if [[ "$mode" == public ]]; then
  grep -qx 'CONFIG_INITRAMFS_SOURCE=""' "$config"
else
  cmp build/keep/lib/firmware/mtcal.bin build/initramfs-root/lib/firmware/mtcal.bin
  for marker in ttyGS0 10.55.0.2; do
    LC_ALL=C grep -aFq "$marker" build/linux/usr/initramfs_data.cpio
  done
  python3 scripts/cpio-owners.py build/linux/usr/initramfs_data.cpio > output/cpio-owners.txt
  test "$(head -n 1 output/cpio-owners.txt)" = '-'
  cat build/out/zImage output/p105ap.dtb > output/zImage-dtb
  bash build-bundle.sh > logs/bundle.log 2>&1
  cp output/linux-boot.bin output/staging-loader.bin
  test "$(stat -c%s output/staging-bundle.bin)" = "$(( $(stat -c%s output/zImage-dtb) + 32768 ))"
fi
cp build/out/zImage /export/zImage
cp output/p105ap.dtb /export/p105ap.dtb
cp "$config" /export/kernel.config
cp build/linux/include/config/kernel.release /export/kernel.release
arm-linux-gnueabihf-gcc --version > /export/compiler.txt
arm-linux-gnueabihf-ld --version > /export/binutils.txt
if [[ "$mode" == private ]]; then
  cp output/staging-bundle.bin output/staging-loader.bin /export/
  # The runtime installer must receive the tools from this build.
  tar -cf /export/initramfs.tar -C build/initramfs-root .
fi

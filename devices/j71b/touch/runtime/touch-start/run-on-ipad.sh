#!/bin/sh
# Explicit stages only; no retries, forced unload, or automatic cleanup.
set -eu
cd "$(dirname "$0")"
[ "$(uname -r)" = @KERNEL_RELEASE@ ]
tr '\000' '\n' < /proc/device-tree/compatible | grep -qx apple,j71b
awk '$3 !~ /^(rootfs|ramfs|proc|sysfs|devtmpfs|tmpfs|configfs|devpts|pstore|debugfs)$/ {bad=1}
     $2=="/" && $3=="rootfs" {root=1} END {exit (bad || !root)}' /proc/mounts
sha256sum -c SHA256SUMS
STATE=/run/touch-start
TRACE=/sys/kernel/debug/j71b-sio-002/mailbox
mkdir -p "$STATE"
trap 'dmesg > "$STATE/last-dmesg.log"; if test -r "$TRACE"; then cat "$TRACE" > "$STATE/last-mailbox.log"; fi' EXIT
PLATFORM=/sys/bus/platform/drivers/apple-spi
PDEV=20a08c000.spi
spi_child() {
 set -- /sys/bus/platform/devices/20a08c000.spi/spi_master/spi*/spi*.0
 test "$#" -eq 1 && test -d "$1"
 test "$(readlink -f "$1/of_node")" = /sys/firmware/devicetree/base/soc/spi@20a08c000/touchscreen@0
 basename "$1"
}
channel_in_use() {
 set -- /sys/class/dma/*
 test "$#" -eq 1 && test -d "$1"
 case "$(readlink -f "$1/device")" in */20ae00000.dma-controller/apple-sio-dma.*.auto) ;; *) exit 1;; esac
 cat "$1/in_use"
}
case "${1:-}" in
 start)
    ! grep -Eq "^(virt_dma|apple_sio_core|apple_sio_dma|j71b_dma_bindings|j71b_z2|j71b_touch_start|j71b_spi_dma_reset|j71b_sio_denied_test) " /proc/modules
    dmesg | grep -q J71B_GATE_PASS
    dmesg | grep -q J71B_GATE_STOP
    mkdir "$STATE/start-attempted"
    mkdir -p /lib/firmware/apple
    for name in t8010-smartio.bin j71b-fwstart.z2fw; do
      if test -e "/lib/firmware/apple/$name"; then
        cmp "firmware/$name" "/lib/firmware/apple/$name"
      else
        cp "firmware/$name" "/lib/firmware/apple/$name"
        chmod 600 "/lib/firmware/apple/$name"
      fi
    done
    insmod modules/virt-dma.ko
    insmod modules/apple-sio-core.ko allow_payload_dma=0
    test "$(cat /sys/module/apple_sio_core/parameters/allow_payload_dma)" = N
    insmod modules/apple-sio-dma.ko
    insmod modules/j71b_dma_bindings.ko
    test "$(cat /sys/module/j71b_dma_bindings/parameters/state)" = 0
    test -r "$TRACE"
    echo 'J71B_START_INFRA_READY payload=OFF'
    ;;
 deny)
    mkdir "$STATE/deny-attempted"
    test "$(cat /sys/module/apple_sio_core/parameters/allow_payload_dma)" = N
    insmod modules/j71b_sio_denied_test.ko
    test "$(cat /sys/module/j71b_sio_denied_test/parameters/result)" = 0
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 0
    cat "$TRACE" > "$STATE/denied-mailbox.log"
    rmmod j71b_sio_denied_test
    rmmod apple_sio_dma
    rmmod apple_sio_core
    insmod modules/apple-sio-core.ko allow_payload_dma=0
    insmod modules/apple-sio-dma.ko
    touch "$STATE/deny-pass"
    echo 'J71B_START_DENIAL_DRAIN_PASS'
    ;;
 attach-dry)
    test -f "$STATE/deny-pass"
    test "$(cat /sys/module/apple_sio_core/parameters/allow_payload_dma)" = N
    mkdir "$STATE/attach-attempted"
    printf '%s\n' "$PDEV" > "$PLATFORM/unbind"
    printf 'attach\n' > /dev/j71b_dma_bindings
    test "$(cat /sys/module/j71b_dma_bindings/parameters/state)" = 1
    printf '%s\n' "$PDEV" > "$PLATFORM/bind"
    SPI=$(spi_child)
    test "$(channel_in_use)" = 1
    test "$(cat /sys/bus/spi/devices/$SPI/statistics/transfers)" = 0
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 0
    touch "$STATE/dry-pass"
    echo 'J71B_START_DRY_PASS dma-attached max-hz=4000000 no-transfer'
    ;;
 enable)
    test -f "$STATE/dry-pass"
    mkdir "$STATE/enable-attempted"
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 0
    printf '%s\n' "$PDEV" > "$PLATFORM/unbind"
    test "$(channel_in_use)" = 0
    rmmod apple_sio_dma
    rmmod apple_sio_core
    insmod modules/apple-sio-core.ko allow_payload_dma=1
    insmod modules/apple-sio-dma.ko
    printf '%s\n' "$PDEV" > "$PLATFORM/bind"
    test "$(channel_in_use)" = 1
    test "$(cat /sys/module/apple_sio_core/parameters/allow_payload_dma)" = Y
    echo 'J71B_START_GATE_ENABLED no-transfer'
    ;;
 arm-reset)
    mkdir "$STATE/arm-reset-attempted"
    SPI=$(spi_child)
    test ! -L /sys/bus/spi/devices/$SPI/driver
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 0
    insmod modules/j71b_spi_dma_reset.ko
    printf 'j71b-dma-reset\n' > /sys/bus/spi/devices/$SPI/driver_override
    echo 'J71B_START_RESET_ARMED no-transfer'
    ;;
 reset)
    mkdir "$STATE/reset-attempted"
    SPI=$(spi_child)
    test "$(cat /sys/bus/spi/devices/$SPI/driver_override)" = j71b-dma-reset
    printf '%s\n' "$SPI" > /sys/bus/spi/drivers/j71b-dma-reset/bind
    test "$(cat /sys/module/j71b_spi_dma_reset/parameters/result)" = 0
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 1
    echo 'J71B_START_RESET_PASS 229524-zero-bytes bpw=16 max-hz=4000000 reset-never-released'
    ;;
 arm-firmware)
    test "$(cat "$STATE/reset.exit")" = 0
    test "$(cat /sys/module/j71b_spi_dma_reset/parameters/result)" = 0
    mkdir "$STATE/arm-firmware-attempted"
    cat "$TRACE" > "$STATE/reset-passed-mailbox.log"
    SPI=$(spi_child)
    printf '%s\n' "$SPI" > /sys/bus/spi/drivers/j71b-dma-reset/unbind
    rmmod j71b_spi_dma_reset
    insmod modules/j71b_touch_start.ko
    test "$(cat /sys/module/j71b_touch_start/parameters/result)" = -115
    printf 'j71b-fw-startup\n' > /sys/bus/spi/devices/$SPI/driver_override
    echo 'J71B_START_FIRMWARE_ARMED no-firmware-sent'
    ;;
 firmware)
    mkdir "$STATE/firmware-attempted"
    test "$(cat "$STATE/reset.exit")" = 0
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 1
    SPI=$(spi_child)
    test "$(cat /sys/bus/spi/devices/$SPI/driver_override)" = j71b-fw-startup
    printf '%s\n' "$SPI" > /sys/bus/spi/drivers/j71b-fw-startup/bind
    test "$(cat /sys/module/j71b_touch_start/parameters/result)" = 0
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 6
    echo 'J71B_START_FIRMWARE_PASS DeviceInfo-valid input=disabled'
    ;;
 restore)
    # An exited, terminal probe is required; never clear an unknown DMA hold.
    if test -d /sys/module/j71b_touch_start; then
      test -f "$STATE/firmware.exit"
      test "$(cat /sys/module/j71b_touch_start/parameters/result)" != -115
    else
      test -f "$STATE/reset.exit"
      test "$(cat /sys/module/j71b_spi_dma_reset/parameters/result)" != -115
    fi
    mkdir "$STATE/restore-attempted"
    cat "$TRACE" > "$STATE/final-mailbox.log"
    printf '%s\n' "$PDEV" > "$PLATFORM/unbind"
    printf 'detach\n' > /dev/j71b_dma_bindings
    test "$(cat /sys/module/j71b_dma_bindings/parameters/state)" = 0
    if test -d /sys/module/j71b_touch_start; then rmmod j71b_touch_start; fi
    if test -d /sys/module/j71b_spi_dma_reset; then rmmod j71b_spi_dma_reset; fi
    rmmod j71b_dma_bindings
    printf '%s\n' "$PDEV" > "$PLATFORM/bind"
    rmmod apple_sio_dma
    rmmod apple_sio_core
    rmmod virt_dma
    ! grep -Eq "^(virt_dma|apple_sio_core|apple_sio_dma|j71b_dma_bindings|j71b_z2|j71b_touch_start|j71b_spi_dma_reset|j71b_sio_denied_test) " /proc/modules
    test ! -e /proc/device-tree/soc/spi@20a08c000/dmas
    test ! -e /proc/device-tree/soc/spi@20a08c000/touchscreen@0/apple,firmware-startup-only
    echo 'J71B_START_RESTORED original-frequency PIO-only SIO-modules-absent'
    ;;
 *) echo 'Choose start | deny | attach-dry | enable | arm-reset | reset | arm-firmware | firmware | restore'; exit 2;;
esac

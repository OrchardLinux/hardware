#!/bin/sh
set -eu
cd "$(dirname "$0")"
[ "$(uname -r)" = @KERNEL_RELEASE@ ]
tr '\000' '\n' < /proc/device-tree/compatible | grep -qx apple,j71b
awk '$3 !~ /^(rootfs|ramfs|proc|sysfs|devtmpfs|tmpfs|configfs|devpts|pstore|debugfs)$/ {bad=1}
     $2=="/" && $3=="rootfs" {root=1} END {exit (bad || !root)}' /proc/mounts
sha256sum -c SHA256SUMS
STATE=/run/touch-runtime
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
input_node() {
 SPI=$(spi_child)
 set -- /sys/bus/spi/devices/$SPI/input/input*/event*
 test "$#" -eq 1 && test -d "$1"
 EVENT=$(basename "$1")
 test "$(cat /sys/class/input/$EVENT/device/name)" = 'J71b Z2 Touchscreen (experimental)'
 test -c "/dev/input/$EVENT"
 printf '/dev/input/%s\n' "$EVENT"
}
case "${1:-}" in
 start)
    ! grep -Eq "^(virt_dma|apple_sio_core|apple_sio_dma|j71b_dma_bindings|j71b_z2|j71b_touch_start|j71b_spi_dma_reset|j71b_sio_denied_test) " /proc/modules
    # Require successful reset and firmware startup from this boot.
    test "$(cat /run/touch-start/reset.exit)" = 0
    test "$(cat /run/touch-start/firmware.exit)" = 0
    mkdir "$STATE/start-attempted"
    cmp firmware/t8010-smartio.bin /lib/firmware/apple/t8010-smartio.bin
    cmp firmware/j71b-fwstart.z2fw /lib/firmware/apple/j71b-fwstart.z2fw
    insmod modules/virt-dma.ko
    insmod modules/apple-sio-core.ko allow_payload_dma=0
    test "$(cat /sys/module/apple_sio_core/parameters/allow_payload_dma)" = N
    insmod modules/apple-sio-dma.ko
    insmod modules/j71b_dma_bindings.ko
    test "$(cat /sys/module/j71b_dma_bindings/parameters/state)" = 0
    test -r "$TRACE"
    echo 'J71B_RUNTIME_INFRA_READY payload=OFF'
    ;;
 attach-dry)
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
    echo 'J71B_RUNTIME_DRY_PASS no-transfer'
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
    echo 'J71B_RUNTIME_GATE_ENABLED no-transfer'
    ;;
 arm)
    mkdir "$STATE/arm-attempted"
    SPI=$(spi_child)
    test ! -L /sys/bus/spi/devices/$SPI/driver
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 0
    insmod modules/j71b_z2.ko enable_runtime=1
    test "$(cat /sys/module/j71b_z2/parameters/result)" = -115
    test "$(cat /sys/module/j71b_z2/parameters/enable_runtime)" = Y
    printf 'j71b-z2-runtime\n' > /sys/bus/spi/devices/$SPI/driver_override
    echo 'J71B_RUNTIME_ARMED no-firmware-sent'
    ;;
 activate)
    mkdir "$STATE/activate-attempted"
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 0
    SPI=$(spi_child)
    test "$(cat /sys/bus/spi/devices/$SPI/driver_override)" = j71b-z2-runtime
    printf '%s\n' "$SPI" > /sys/bus/spi/drivers/j71b-z2-runtime/bind
    test "$(cat /sys/module/j71b_z2/parameters/result)" = 0
    test "$(cat /sys/module/apple_sio_core/parameters/payload_submits)" = 5
    input_node > "$STATE/event-node"
    echo "J71B_RUNTIME_INPUT_READY node=$(cat "$STATE/event-node") gestures-not-yet-tested"
    ;;
 record)
    test "$(cat "$STATE/activate.exit")" = 0
    mkdir "$STATE/record-attempted"
    NODE=$(input_node)
    test "$NODE" = "$(cat "$STATE/event-node")"
    chmod 700 ./record-touch
    echo "J71B_RUNTIME_RECORD_START node=$NODE seconds=300"
    ./record-touch "$NODE" 300 > "$STATE/events.jsonl"
    echo 'J71B_RUNTIME_RECORD_COMPLETE'
    ;;
 restore)
    test -f "$STATE/activate.exit"
    test "$(cat /sys/module/j71b_z2/parameters/result)" != -115
    if test -d "$STATE/record-launched"; then test -f "$STATE/record.exit"; fi
    mkdir "$STATE/restore-attempted"
    cat "$TRACE" > "$STATE/final-mailbox.log"
    printf '%s\n' "$PDEV" > "$PLATFORM/unbind"
    printf 'detach\n' > /dev/j71b_dma_bindings
    test "$(cat /sys/module/j71b_dma_bindings/parameters/state)" = 0
    rmmod j71b_z2
    rmmod j71b_dma_bindings
    printf '%s\n' "$PDEV" > "$PLATFORM/bind"
    rmmod apple_sio_dma
    rmmod apple_sio_core
    rmmod virt_dma
    ! grep -Eq "^(virt_dma|apple_sio_core|apple_sio_dma|j71b_dma_bindings|j71b_z2|j71b_touch_start|j71b_spi_dma_reset|j71b_sio_denied_test) " /proc/modules
    test ! -e /proc/device-tree/soc/spi@20a08c000/dmas
    echo 'J71B_RUNTIME_RESTORED PIO-only SIO-modules-absent'
    ;;
 *) echo 'Choose start | attach-dry | enable | arm | activate | record | restore'; exit 2;;
esac

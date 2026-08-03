#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ADB="${ADB:-adb}"

if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "Missing adb command: $ADB" >&2
    exit 1
fi

echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Read-only I2C topology probe ==="
"$ADB" shell sh -s <<'SH'
set -eu

safe_cat() {
    [ -r "$1" ] && cat "$1" || true
}

safe_link() {
    readlink -f "$1" 2>/dev/null || true
}

echo "kernel=$(uname -r 2>/dev/null || true)"

echo "=== I2C adapters ==="
for adapter in /sys/class/i2c-adapter/i2c-*; do
    [ -e "$adapter" ] || continue
    bus="${adapter##*-}"
    echo "adapter_begin=$bus"
    echo "adapter_class=$adapter"
    echo "adapter_target=$(safe_link "$adapter")"
    echo "adapter_device_target=$(safe_link "$adapter/device")"
    echo "adapter_name=$(safe_cat "$adapter/name")"
    if [ -r "/sys/class/i2c-dev/i2c-$bus/dev" ]; then
        echo "i2c_dev_number=$(safe_cat "/sys/class/i2c-dev/i2c-$bus/dev")"
    else
        echo "i2c_dev_number=absent"
    fi
    echo "adapter_end=$bus"
done

echo "=== I2C clients ==="
for client in /sys/bus/i2c/devices/[0-9]*-00*; do
    [ -e "$client" ] || continue
    base="${client##*/}"
    echo "client_begin=$base"
    echo "client_target=$(safe_link "$client")"
    echo "client_name=$(safe_cat "$client/name")"
    echo "client_driver=$(safe_link "$client/driver")"
    echo "client_modalisa=$(safe_cat "$client/modalias")"
    echo "client_end=$base"
done

echo "=== S2MU106-focused clients ==="
for client in \
    /sys/bus/i2c/devices/6-003b \
    /sys/bus/i2c/devices/6-003c \
    /sys/bus/i2c/devices/6-003d \
    /sys/bus/i2c/devices/6-003e
 do
    base="${client##*/}"
    if [ -e "$client" ]; then
        echo "focused_client=$base"
        echo "focused_target=$(safe_link "$client")"
        echo "focused_name=$(safe_cat "$client/name")"
        echo "focused_driver=$(safe_link "$client/driver")"
        echo "focused_modalias=$(safe_cat "$client/modalias")"
    else
        echo "focused_client=$base absent"
    fi
 done

echo "=== Kernel evidence ==="
dmesg 2>/dev/null | grep -Ei \
    's2mu106|muic|usbpd|6-003[bcde]|138[0-9a-f]+\.hsi2c' | tail -n 500 || true

# Fail closed only on facts already established by the recovered U0f log.
if [ ! -e /sys/bus/i2c/devices/6-003b ]; then
    echo "REFUSING: expected S2MU106 fuel-gauge client 6-003b is absent" >&2
    exit 1
fi
if [ ! -e /sys/bus/i2c/devices/6-003c ]; then
    echo "REFUSING: expected S2MU106 USB-PD client 6-003c is absent" >&2
    exit 1
fi

bus6_target="$(safe_link /sys/class/i2c-adapter/i2c-6)"
echo "bus6_target=$bus6_target"
if [ -z "$bus6_target" ]; then
    echo "REFUSING: I2C bus 6 adapter target is unavailable" >&2
    exit 1
fi

echo "s2mu106_topology_probe=passed"
SH

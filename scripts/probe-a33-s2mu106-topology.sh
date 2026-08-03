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

show_client() {
    client="$1"
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
}

echo "kernel=$(uname -r 2>/dev/null || true)"

echo "=== I2C adapters ==="
# Some TWRP builds do not populate /sys/class/i2c-adapter. The canonical
# i2c-core adapter nodes under /sys/bus/i2c/devices remain available.
for adapter in /sys/bus/i2c/devices/i2c-*; do
    [ -e "$adapter" ] || continue
    bus="${adapter##*-}"
    echo "adapter_begin=$bus"
    echo "adapter_node=$adapter"
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
    echo "client_modalias=$(safe_cat "$client/modalias")"
    echo "client_end=$base"
done

echo "=== S2MU106-focused clients ==="
# TWRP exposes the MFD/MUIC banks on 13860000.hsi2c and the fuel-gauge/PD
# banks on 138b0000.hsi2c. They must not be collapsed into one assumed bus.
show_client /sys/bus/i2c/devices/2-003d
show_client /sys/bus/i2c/devices/2-003e
show_client /sys/bus/i2c/devices/6-003b
show_client /sys/bus/i2c/devices/6-003c

echo "=== Kernel evidence ==="
dmesg 2>/dev/null | grep -Ei \
    's2mu106|muic|usbpd|2-003[de]|6-003[bc]|13860000\.hsi2c|138b0000\.hsi2c' \
    | tail -n 500 || true

mfd=/sys/bus/i2c/devices/2-003d
muic=/sys/bus/i2c/devices/2-003e
fuel=/sys/bus/i2c/devices/6-003b
pd=/sys/bus/i2c/devices/6-003c

for required in "$mfd" "$muic" "$fuel" "$pd"; do
    if [ ! -e "$required" ]; then
        echo "REFUSING: expected S2MU106 client is absent: ${required##*/}" >&2
        exit 1
    fi
done

mfd_target="$(safe_link "$mfd")"
muic_target="$(safe_link "$muic")"
fuel_target="$(safe_link "$fuel")"
pd_target="$(safe_link "$pd")"

case "$mfd_target" in
    */13860000.hsi2c/i2c-2/2-003d) ;;
    *) echo "REFUSING: unexpected S2MU106 MFD target: $mfd_target" >&2; exit 1 ;;
esac
case "$muic_target" in
    */13860000.hsi2c/i2c-2/2-003e) ;;
    *) echo "REFUSING: unexpected MUIC bank target: $muic_target" >&2; exit 1 ;;
esac
case "$fuel_target" in
    */138b0000.hsi2c/i2c-6/6-003b) ;;
    *) echo "REFUSING: unexpected fuel-gauge target: $fuel_target" >&2; exit 1 ;;
esac
case "$pd_target" in
    */138b0000.hsi2c/i2c-6/6-003c) ;;
    *) echo "REFUSING: unexpected USB-PD target: $pd_target" >&2; exit 1 ;;
esac

mfd_name="$(safe_cat "$mfd/name")"
muic_name="$(safe_cat "$muic/name")"
if [ "$mfd_name" != "s2mu106mfd" ]; then
    echo "REFUSING: unexpected 2-003d name: ${mfd_name:-missing}" >&2
    exit 1
fi
if [ "$muic_name" != "dummy" ]; then
    echo "REFUSING: unexpected 2-003e name: ${muic_name:-missing}" >&2
    exit 1
fi

echo "muic_controller=13860000.hsi2c"
echo "twrp_muic_bus=2"
echo "muic_address=0x3e"
echo "fuel_pd_controller=138b0000.hsi2c"
echo "fuel_pd_bus=6"
echo "s2mu106_topology_probe=passed"
SH

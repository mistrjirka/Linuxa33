#!/usr/bin/env bash
set -euo pipefail

ADB="${ADB:-adb}"
DEVICE="${DEVICE:-/dev/block/by-name/metadata}"
MOUNTPOINT="${MOUNTPOINT:-/tmp/a33x-metadata-ro}"

if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "Missing adb command: $ADB" >&2
    exit 1
fi

echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Read-only metadata probe ==="
"$ADB" shell sh -s -- "$DEVICE" "$MOUNTPOINT" <<'SH'
set -eu

DEVICE="$1"
MOUNTPOINT="$2"

if [ ! -b "$DEVICE" ]; then
    echo "REFUSING: metadata block device is missing: $DEVICE" >&2
    exit 1
fi

resolved="$(readlink -f "$DEVICE" 2>/dev/null || true)"
echo "device=$DEVICE"
echo "resolved=${resolved:-unknown}"

if command -v blockdev >/dev/null 2>&1; then
    size="$(blockdev --getsize64 "$DEVICE" 2>/dev/null || true)"
    [ -n "$size" ] && echo "size_bytes=$size"
fi

if command -v file >/dev/null 2>&1; then
    echo "=== file signature ==="
    file -s "$DEVICE" 2>&1 || true
fi

if command -v od >/dev/null 2>&1; then
    echo "=== first 256 bytes ==="
    dd if="$DEVICE" bs=256 count=1 2>/dev/null | od -Ax -tx1
fi

mkdir -p "$MOUNTPOINT"
umount "$MOUNTPOINT" 2>/dev/null || true

if ! mount -t ext4 -o ro,noload "$DEVICE" "$MOUNTPOINT"; then
    echo "REFUSING: metadata did not mount read-only as ext4 with noload" >&2
    exit 1
fi

cleanup() {
    umount "$MOUNTPOINT" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== mounted metadata ==="
mount | grep " $MOUNTPOINT " || true

echo "=== root entries ==="
ls -la "$MOUNTPOINT"

echo "=== free space ==="
df -h "$MOUNTPOINT"

echo "metadata_readonly_probe=passed"
SH

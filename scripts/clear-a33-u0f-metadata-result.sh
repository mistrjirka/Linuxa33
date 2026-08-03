#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ADB="${ADB:-adb}"
DEVICE="${DEVICE:-/dev/block/by-name/metadata}"
MOUNTPOINT="${MOUNTPOINT:-/tmp/a33x-metadata-clear}"
RELATIVE_DIR="${RELATIVE_DIR:-a33x-bringup}"
RELATIVE_FILE="${RELATIVE_FILE:-$RELATIVE_DIR/u0f-muic-result.txt}"

if ! command -v "$ADB" >/dev/null 2>&1; then
    echo "Missing adb command: $ADB" >&2
    exit 1
fi

echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Clear only the stale U0f metadata result ==="
"$ADB" shell sh -s -- \
    "$DEVICE" "$MOUNTPOINT" "$RELATIVE_DIR" "$RELATIVE_FILE" <<'SH'
set -eu

DEVICE="$1"
MOUNTPOINT="$2"
RELATIVE_DIR="$3"
RELATIVE_FILE="$4"

if [ ! -b "$DEVICE" ]; then
    echo "REFUSING: metadata block device is missing: $DEVICE" >&2
    exit 1
fi

resolved="$(readlink -f "$DEVICE" 2>/dev/null || true)"
if grep -qE "^(${DEVICE}|${resolved}) " /proc/mounts 2>/dev/null; then
    echo "REFUSING: metadata block device is already mounted" >&2
    grep -E "^(${DEVICE}|${resolved}) " /proc/mounts >&2 || true
    exit 1
fi

mkdir -p "$MOUNTPOINT"
umount "$MOUNTPOINT" 2>/dev/null || true

cleanup() {
    umount "$MOUNTPOINT" 2>/dev/null || true
}
trap cleanup EXIT

if ! mount -t ext4 -o rw,nosuid,nodev,noatime "$DEVICE" "$MOUNTPOINT"; then
    echo "REFUSING: metadata did not mount read-write as ext4" >&2
    exit 1
fi

TARGET="$MOUNTPOINT/$RELATIVE_FILE"
TEMPORARY="$TARGET.tmp"
rm -f "$TARGET" "$TEMPORARY"
sync
umount "$MOUNTPOINT"

if ! mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$DEVICE" "$MOUNTPOINT"; then
    echo "REFUSING: metadata did not remount read-only for verification" >&2
    exit 1
fi

if [ -e "$MOUNTPOINT/$RELATIVE_FILE" ] || [ -e "$MOUNTPOINT/$RELATIVE_FILE.tmp" ]; then
    echo "REFUSING: U0f result remained after cleanup" >&2
    exit 1
fi

if [ -d "$MOUNTPOINT/$RELATIVE_DIR" ]; then
    echo "=== retained dedicated directory contents ==="
    ls -la "$MOUNTPOINT/$RELATIVE_DIR"
fi

umount "$MOUNTPOINT"
trap - EXIT

echo "u0f_metadata_result_cleanup=passed"
echo "cleared_path=/$RELATIVE_FILE"
SH

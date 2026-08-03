#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ADB="${ADB:-adb}"
DEVICE="${DEVICE:-/dev/block/by-name/metadata}"
MOUNTPOINT="${MOUNTPOINT:-/tmp/a33x-metadata-test}"
RELATIVE_DIR="${RELATIVE_DIR:-a33x-bringup}"
RELATIVE_FILE="${RELATIVE_FILE:-$RELATIVE_DIR/persistence-test.txt}"
MARKER="a33x_metadata_persistence_v1_$(date +%Y%m%d-%H%M%S)"
EXPECTED_SHA256="$(printf '%s\n' "$MARKER" | sha256sum | awk '{print $1}')"

for command in "$ADB" sha256sum awk date grep; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Controlled metadata write, unmount, and read-only verification ==="
"$ADB" shell sh -s -- \
    "$DEVICE" "$MOUNTPOINT" "$RELATIVE_DIR" "$RELATIVE_FILE" \
    "$MARKER" "$EXPECTED_SHA256" <<'SH'
set -eu

DEVICE="$1"
MOUNTPOINT="$2"
RELATIVE_DIR="$3"
RELATIVE_FILE="$4"
MARKER="$5"
EXPECTED_SHA256="$6"
TARGET="$MOUNTPOINT/$RELATIVE_FILE"

if [ ! -b "$DEVICE" ]; then
    echo "REFUSING: metadata block device is missing: $DEVICE" >&2
    exit 1
fi

resolved="$(readlink -f "$DEVICE" 2>/dev/null || true)"
echo "device=$DEVICE"
echo "resolved=${resolved:-unknown}"

# Do not touch the filesystem if Android/TWRP already mounted this block device
# elsewhere. This avoids a second simultaneous mount of the same ext4 volume.
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

echo "=== read-write mount ==="
mount | grep " $MOUNTPOINT " || true

if [ -e "$TARGET" ]; then
    echo "REFUSING: test target already exists: /$RELATIVE_FILE" >&2
    exit 1
fi

mkdir -p "$MOUNTPOINT/$RELATIVE_DIR"
chmod 0700 "$MOUNTPOINT/$RELATIVE_DIR"
printf '%s\n' "$MARKER" > "$TARGET"
chmod 0600 "$TARGET"
sync

written_sha256="$(sha256sum "$TARGET" | awk '{print $1}')"
echo "written_sha256=$written_sha256"
if [ "$written_sha256" != "$EXPECTED_SHA256" ]; then
    echo "REFUSING: write-time SHA256 mismatch" >&2
    exit 1
fi

umount "$MOUNTPOINT"

if ! mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$DEVICE" "$MOUNTPOINT"; then
    echo "REFUSING: metadata did not remount read-only for verification" >&2
    exit 1
fi

echo "=== read-only remount ==="
mount | grep " $MOUNTPOINT " || true

if [ ! -f "$TARGET" ]; then
    echo "REFUSING: persisted test file is missing after remount" >&2
    exit 1
fi

verified_sha256="$(sha256sum "$TARGET" | awk '{print $1}')"
verified_marker="$(cat "$TARGET")"

echo "verified_marker=$verified_marker"
echo "verified_sha256=$verified_sha256"

if [ "$verified_sha256" != "$EXPECTED_SHA256" ]; then
    echo "REFUSING: persisted SHA256 mismatch" >&2
    exit 1
fi
if [ "$verified_marker" != "$MARKER" ]; then
    echo "REFUSING: persisted marker mismatch" >&2
    exit 1
fi

umount "$MOUNTPOINT"
trap - EXIT

echo "metadata_persistence_test=passed"
echo "persistent_path=/$RELATIVE_FILE"
echo "persistent_sha256=$verified_sha256"
SH

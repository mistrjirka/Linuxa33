#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
METADATA_DEVICE="${METADATA_DEVICE:-/dev/block/by-name/metadata}"
METADATA_MOUNTPOINT="${METADATA_MOUNTPOINT:-/tmp/a33x-metadata-u0g-precheck}"
METADATA_RESULT_RELATIVE="a33x-bringup/u0g-muic-result.txt"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_COLLECTOR="$SCRIPT_DIR/collect-a33-previous-boot.sh"

for required in "$BASE_COLLECTOR"; do
    if [[ ! -x "$required" && ! -f "$required" ]]; then
        echo "Missing required collector: $required" >&2
        exit 1
    fi
done

until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Require persistent U0g result before general collection ==="
"$ADB" shell sh -s -- \
    "$METADATA_DEVICE" "$METADATA_MOUNTPOINT" "$METADATA_RESULT_RELATIVE" <<'SH'
set -eu
DEVICE="$1"
MOUNTPOINT="$2"
RELATIVE="$3"

if [ ! -b "$DEVICE" ]; then
    echo "REFUSING: metadata block device missing: $DEVICE" >&2
    exit 1
fi

resolved="$(readlink -f "$DEVICE" 2>/dev/null || true)"
existing_mount="$(awk -v a="$DEVICE" -v b="$resolved" '$1==a || $1==b {print $2; exit}' /proc/mounts 2>/dev/null || true)"
mounted_here=no
if [ -n "$existing_mount" ]; then
    root="$existing_mount"
else
    mkdir -p "$MOUNTPOINT"
    umount "$MOUNTPOINT" 2>/dev/null || true
    mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$DEVICE" "$MOUNTPOINT"
    root="$MOUNTPOINT"
    mounted_here=yes
fi
cleanup() {
    [ "$mounted_here" = no ] || umount "$MOUNTPOINT" 2>/dev/null || true
}
trap cleanup EXIT

target="$root/$RELATIVE"
if [ ! -s "$target" ]; then
    echo "REFUSING: persistent U0g result is missing or empty: /$RELATIVE" >&2
    exit 1
fi

echo "u0g_metadata_result_precheck=passed"
echo "u0g_metadata_result_bytes=$(wc -c < "$target")"
echo "u0g_metadata_result_sha256=$(sha256sum "$target" | awk '{print $1}')"
SH

METADATA_RESULT_RELATIVE="$METADATA_RESULT_RELATIVE" \
    bash "$BASE_COLLECTOR" u0g

OUT="$(find "$RESULT_ROOT" -maxdepth 1 -type d -name 'u0g-result-*' -printf '%T@ %p\n' \
    | sort -nr | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}')"
if [[ -z "$OUT" || ! -d "$OUT" ]]; then
    echo "REFUSING: could not locate newly collected U0g result directory" >&2
    exit 1
fi

for source in \
    "$PORT_ROOT/build/u0g-muic-dynamic.txt" \
    "$PORT_ROOT/build/u0g-third-host-prepare.txt" \
    "$PORT_ROOT/build/u0g-third-host-recovery-build.txt" \
    "$PORT_ROOT/build/u0g-host-kernel-live.txt" \
    "$PORT_ROOT/build/u0g-host-lsusb-live.txt" \
    "$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-manifest.txt"
do
    if [[ -f "$source" ]]; then
        cp -a "$source" "$OUT/"
    fi
done

ARCHIVE="$OUT.tar.gz"
tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "U0g previous-boot result collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Persistent result: $OUT/u0f-metadata-result.txt"

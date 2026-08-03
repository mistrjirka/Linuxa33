#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
EXPECTED_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
METADATA_RESULT_RELATIVE="a33x-bringup/u0g-muic-result.txt"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$SCRIPT_DIR/collect-a33-previous-boot.sh"

for command in "$ADB" find sort awk grep tar sha256sum cp date; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
[[ -f "$BASE" ]] || {
    echo "Missing base collector: $BASE" >&2
    exit 1
}

until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done
RECOVERY_SHA="$($ADB shell 'sha256sum /dev/block/by-name/recovery' | awk 'NR==1 {print $1}' | tr -d '\r')"
if [[ "$RECOVERY_SHA" != "$EXPECTED_TWRP_SHA256" ]]; then
    echo "REFUSING: exact known-good TWRP has not been restored" >&2
    echo "expected=$EXPECTED_TWRP_SHA256 actual=${RECOVERY_SHA:-missing}" >&2
    exit 1
fi

METADATA_RESULT_RELATIVE="$METADATA_RESULT_RELATIVE" \
EXPECTED_TWRP_SHA256="$EXPECTED_TWRP_SHA256" \
    bash "$BASE" first-rootfs

OUT="$(
    find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'first-rootfs-result-*' -printf '%T@ %p\n' \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
if [[ -z "$OUT" || ! -d "$OUT" ]]; then
    echo "REFUSING: base first-rootfs result directory was not found" >&2
    exit 1
fi

"$ADB" shell sh -s > "$OUT/userdata-rootfs-readonly-check.txt" <<'SH'
set -u
target=/dev/block/by-name/userdata
mountpoint=/tmp/a33x-first-rootfs-failure-check
resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target=$target"
echo "resolved=$resolved"
echo "bytes=$(blockdev --getsize64 "$target" 2>/dev/null || true)"
echo "type=$(blkid -s TYPE -o value "$target" 2>/dev/null || true)"
echo "label=$(blkid -s LABEL -o value "$target" 2>/dev/null || true)"
echo "uuid=$(blkid -s UUID -o value "$target" 2>/dev/null || true)"
echo "mount_users_begin"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mp; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$source $mp"
    fi
done
echo "mount_users_end"

umount "$mountpoint" 2>/dev/null || true
mkdir -p "$mountpoint"
if mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"; then
    cleanup() { umount "$mountpoint" 2>/dev/null || true; }
    trap cleanup EXIT
    echo "readonly_mount=passed"
    for path in \
        /sbin/init \
        /etc/os-release \
        /usr/sbin/sshd \
        /etc/runlevels/default/sshd \
        /etc/runlevels/default/networkmanager \
        /etc/a33x-rootfs-target; do
        if [ -e "$mountpoint$path" ]; then
            echo "present=$path"
        else
            echo "missing=$path"
        fi
    done
    echo "fstab_begin"
    cat "$mountpoint/etc/fstab" 2>&1 || true
    echo "fstab_end"
    echo "marker_begin"
    cat "$mountpoint/etc/a33x-rootfs-target" 2>&1 || true
    echo "marker_end"
    df -h "$mountpoint" 2>&1 || true
else
    echo "readonly_mount=failed"
fi

if command -v e2fsck >/dev/null 2>&1; then
    echo "e2fsck_readonly_begin"
    e2fsck -fn "$target" 2>&1 || true
    echo "e2fsck_readonly_end"
fi
SH

for source in \
    "$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt" \
    "$PORT_ROOT/build/a33-first-rootfs-u0g-flash.txt" \
    "$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt" \
    "$PORT_ROOT/build/a33-u0g-unified-root-handoff-details.txt" \
    "$PORT_ROOT/build/a33-userdata-rootfs-image.txt"; do
    [[ -f "$source" ]] && cp -a "$source" "$OUT/"
done

LATEST_OBSERVATION="$(
    find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'a33-first-rootfs-observation-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
if [[ -n "$LATEST_OBSERVATION" && -d "$LATEST_OBSERVATION" ]]; then
    mkdir -p "$OUT/observation"
    cp -a "$LATEST_OBSERVATION"/. "$OUT/observation/"
fi

ROOT_TYPE="$(awk -F= '$1=="type" {print $2; exit}' "$OUT/userdata-rootfs-readonly-check.txt")"
ROOT_LABEL="$(awk -F= '$1=="label" {print $2; exit}' "$OUT/userdata-rootfs-readonly-check.txt")"
ROOT_UUID="$(awk -F= '$1=="uuid" {print $2; exit}' "$OUT/userdata-rootfs-readonly-check.txt")"
ROOT_MOUNT="$(awk -F= '$1=="readonly_mount" {print $2; exit}' "$OUT/userdata-rootfs-readonly-check.txt")"

{
    echo "created=$(date -Ins)"
    echo "operation=collect-first-rootfs-previous-boot"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "rootfs_type=${ROOT_TYPE:-unknown}"
    echo "rootfs_label=${ROOT_LABEL:-unknown}"
    echo "rootfs_uuid=${ROOT_UUID:-unknown}"
    echo "rootfs_readonly_mount=${ROOT_MOUNT:-unknown}"
    echo "phone_partition_writes=no"
    echo "collection_status=passed"
} | tee "$OUT/first-rootfs-summary.txt"

ARCHIVE="$OUT.tar.gz"
tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "First-rootfs previous-boot evidence collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"

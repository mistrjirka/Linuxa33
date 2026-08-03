#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ROOT_LINK="${ROOT_LINK:-$PORT_ROOT/build/rootfs-images/current/samsung-a33x-root.img}"
ADB="${ADB:-adb}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$PORT_ROOT/build/a33-rootfs-storage-capacity-v3-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
CACHE_MARGIN_BYTES=$((128 * 1024 * 1024))
MIN_EXTERNAL_BYTES=$((4 * 1024 * 1024 * 1024))

for command in "$ADB" readlink stat sha256sum blkid dumpe2fs resize2fs awk grep sed sort tar; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

ROOT_IMAGE="$(readlink -f "$ROOT_LINK" 2>/dev/null || true)"
if [[ -z "$ROOT_IMAGE" || ! -f "$ROOT_IMAGE" ]]; then
    echo "REFUSING: finalized standalone rootfs image is missing" >&2
    echo "Run scripts/finalize-a33-standalone-rootfs-image.sh first." >&2
    exit 1
fi

ROOT_TYPE="$(blkid -p -s TYPE -o value "$ROOT_IMAGE" 2>/dev/null || true)"
[[ "$ROOT_TYPE" == ext4 ]] || {
    echo "REFUSING: root image is not ext4: $ROOT_IMAGE" >&2
    exit 1
}

ROOT_BLOCK_SIZE="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block size/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_BLOCK_COUNT="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block count/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_FREE_BLOCKS="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Free blocks/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
RESIZE_OUTPUT="$(resize2fs -P "$ROOT_IMAGE" 2>&1)"
ROOT_MIN_BLOCKS="$(
    printf '%s\n' "$RESIZE_OUTPUT" \
    | awk 'match($0, /[0-9]+[[:space:]]*$/) {value=substr($0, RSTART, RLENGTH); gsub(/[[:space:]]/, "", value)} END {print value}'
)"

for value in "$ROOT_BLOCK_SIZE" "$ROOT_BLOCK_COUNT" "$ROOT_FREE_BLOCKS" "$ROOT_MIN_BLOCKS"; do
    [[ "$value" =~ ^[0-9]+$ ]] || {
        echo "REFUSING: root image capacity calculation failed" >&2
        echo "resize2fs_output=$RESIZE_OUTPUT" >&2
        exit 1
    }
done

ROOT_APPARENT_BYTES="$(stat -Lc '%s' "$ROOT_IMAGE")"
ROOT_USED_BYTES=$(( (ROOT_BLOCK_COUNT - ROOT_FREE_BLOCKS) * ROOT_BLOCK_SIZE ))
ROOT_MINIMUM_BYTES=$(( ROOT_MIN_BLOCKS * ROOT_BLOCK_SIZE ))
ROOT_SHA256="$(sha256sum "$ROOT_IMAGE" | awk '{print $1}')"
ROOT_UUID="$(blkid -p -s UUID -o value "$ROOT_IMAGE" 2>/dev/null || true)"
ROOT_LABEL="$(blkid -p -s LABEL -o value "$ROOT_IMAGE" 2>/dev/null || true)"
CACHE_REQUIRED_BYTES=$(( ROOT_MINIMUM_BYTES + CACHE_MARGIN_BYTES ))
REQUIRED_EXTERNAL_BYTES="$MIN_EXTERNAL_BYTES"
if (( ROOT_APPARENT_BYTES * 2 > REQUIRED_EXTERNAL_BYTES )); then
    REQUIRED_EXTERNAL_BYTES=$(( ROOT_APPARENT_BYTES * 2 ))
fi

mkdir -p "$OUT"
{
    echo "created=$(date -Ins)"
    echo "audit_version=3"
    echo "collection_policy=read-only"
    echo "phone_required_mode=TWRP"
    echo "persistent_phone_writes=no"
    echo "block_writes=no"
    echo "mount_operations=no"
    echo "root_image=$ROOT_IMAGE"
    echo "root_sha256=$ROOT_SHA256"
    echo "userdata_directory_contents=not-listed"
    echo "wifi_credentials=not-read"
    echo "ssh_keys=not-read"
} | tee "$OUT/manifest.txt"
printf '%s\n' "$RESIZE_OUTPUT" > "$OUT/root-resize2fs-P.txt"

echo "=== Wait for exact known-good TWRP ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

RECOVERY_SHA="$($ADB shell 'sha256sum /dev/block/by-name/recovery 2>/dev/null' | awk 'NR==1 {print $1}' | tr -d '\r')"
if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running exact known-good TWRP" >&2
    echo "expected=$KNOWN_TWRP_SHA256" >&2
    echo "actual=${RECOVERY_SHA:-missing}" >&2
    exit 1
fi

"$ADB" shell sh -s > "$OUT/twrp-storage.txt" <<'SH'
set -u

safe_link() {
    readlink -f "$1" 2>/dev/null || true
}

block_bytes() {
    blockdev --getsize64 "$1" 2>/dev/null || true
}

mount_source() {
    awk -v mountpoint="$1" '$2==mountpoint {print $1; exit}' /proc/mounts 2>/dev/null || true
}

echo "mode=TWRP"
echo "kernel=$(uname -r 2>/dev/null || true)"
echo "recovery_hash=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"

echo "=== named internal partitions ==="
for name in cache userdata metadata super boot recovery; do
    path="/dev/block/by-name/$name"
    echo "candidate_begin=$name"
    echo "candidate_path=$path"
    echo "candidate_resolved=$(safe_link "$path")"
    if [ -b "$path" ]; then
        echo "candidate_is_block=yes"
        echo "candidate_bytes=$(block_bytes "$path")"
        echo "candidate_blkid=$(blkid "$path" 2>/dev/null | tr '\n\r' '  ')"
    else
        echo "candidate_is_block=no"
    fi
    echo "candidate_end=$name"
done

echo "=== encryption and mount state ==="
for prop in ro.crypto.state ro.crypto.type ro.crypto.metadata.enabled twrp.decrypt.done; do
    echo "$prop=$(getprop "$prop" 2>/dev/null || true)"
done
echo "data_mount_source=$(mount_source /data)"
echo "cache_mount_source=$(mount_source /cache)"

echo "=== all non-virtual block devices ==="
for sys in /sys/class/block/*; do
    [ -e "$sys" ] || continue
    base="${sys##*/}"
    case "$base" in loop*|ram*|zram*) continue ;; esac
    dev="/dev/block/$base"
    [ -b "$dev" ] || dev="/dev/$base"
    echo "block_begin=$base"
    echo "block_dev=$dev"
    echo "block_bytes=$(block_bytes "$dev")"
    echo "block_removable=$(cat "$sys/removable" 2>/dev/null || true)"
    echo "block_partition=$(cat "$sys/partition" 2>/dev/null || true)"
    echo "block_blkid=$(blkid "$dev" 2>/dev/null | tr '\n\r' '  ')"
    echo "block_end=$base"
done

echo "=== removable whole-device candidates ==="
for sys in /sys/class/block/*; do
    [ -e "$sys" ] || continue
    removable="$(cat "$sys/removable" 2>/dev/null || true)"
    partition="$(cat "$sys/partition" 2>/dev/null || true)"
    [ "$removable" = 1 ] || continue
    [ -z "$partition" ] || continue
    base="${sys##*/}"
    dev="/dev/block/$base"
    [ -b "$dev" ] || dev="/dev/$base"
    echo "removable_device=$base"
    echo "removable_dev=$dev"
    echo "removable_bytes=$(block_bytes "$dev")"
    echo "removable_blkid=$(blkid "$dev" 2>/dev/null | tr '\n\r' '  ')"
done
SH

TOPOLOGY="$OUT/twrp-storage.txt"
value_for_candidate() {
    local candidate="$1" key="$2"
    awk -F= -v candidate="$candidate" -v key="$key" '
        $0=="candidate_begin=" candidate {inside=1; next}
        $0=="candidate_end=" candidate {inside=0}
        inside && $1==key {print substr($0, length(key)+2); exit}
    ' "$TOPOLOGY"
}

CACHE_BYTES="$(value_for_candidate cache candidate_bytes)"
USERDATA_BYTES="$(value_for_candidate userdata candidate_bytes)"
DATA_MOUNT_SOURCE="$(awk -F= '$1=="data_mount_source" {print substr($0, length($1)+2); exit}' "$TOPOLOGY")"
CRYPTO_STATE="$(awk -F= '$1=="ro.crypto.state" {print substr($0, length($1)+2); exit}' "$TOPOLOGY")"
CRYPTO_TYPE="$(awk -F= '$1=="ro.crypto.type" {print substr($0, length($1)+2); exit}' "$TOPOLOGY")"
REMOVABLE_COUNT="$(grep -c '^removable_device=' "$TOPOLOGY" || true)"
REMOVABLE_MAX_BYTES="$(awk -F= '$1=="removable_bytes" && $2 ~ /^[0-9]+$/ {if ($2+0 > max) max=$2+0} END {if (max) printf "%.0f\n", max}' "$TOPOLOGY")"

CACHE_RESULT="unknown"
if [[ "$CACHE_BYTES" =~ ^[0-9]+$ ]]; then
    if (( CACHE_BYTES >= CACHE_REQUIRED_BYTES )); then
        CACHE_RESULT="fits-minimum-with-128MiB-margin"
    else
        CACHE_RESULT="too-small-for-minimum-plus-128MiB-margin"
    fi
fi

USERDATA_RESULT="not-approved-early-boot-access-unproven"
if [[ -n "$DATA_MOUNT_SOURCE" ]]; then
    USERDATA_RESULT="twrp-mounted-but-linux-initramfs-FBE-access-unproven"
fi

DECISION="no-safe-rootfs-target-confirmed"
if [[ "$REMOVABLE_MAX_BYTES" =~ ^[0-9]+$ ]] && (( REMOVABLE_MAX_BYTES >= REQUIRED_EXTERNAL_BYTES )); then
    DECISION="external-removable-rootfs-preferred"
elif [[ "$CACHE_RESULT" == fits-minimum-with-128MiB-margin ]]; then
    DECISION="cache-may-fit-minimal-rootfs-but-requires-controlled-deployment-and-backup"
fi

{
    echo "audit_version=3"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "root_image=$ROOT_IMAGE"
    echo "root_sha256=$ROOT_SHA256"
    echo "root_uuid=${ROOT_UUID:-none}"
    echo "root_label=${ROOT_LABEL:-none}"
    echo "root_apparent_bytes=$ROOT_APPARENT_BYTES"
    echo "root_used_bytes=$ROOT_USED_BYTES"
    echo "root_minimum_blocks=$ROOT_MIN_BLOCKS"
    echo "root_minimum_bytes=$ROOT_MINIMUM_BYTES"
    echo "cache_bytes=${CACHE_BYTES:-unknown}"
    echo "cache_required_bytes=$CACHE_REQUIRED_BYTES"
    echo "cache_capacity_result=$CACHE_RESULT"
    echo "userdata_bytes=${USERDATA_BYTES:-unknown}"
    echo "userdata_mount_source=${DATA_MOUNT_SOURCE:-none}"
    echo "userdata_crypto_state=${CRYPTO_STATE:-unknown}"
    echo "userdata_crypto_type=${CRYPTO_TYPE:-unknown}"
    echo "userdata_result=$USERDATA_RESULT"
    echo "removable_device_count=$REMOVABLE_COUNT"
    echo "removable_max_bytes=${REMOVABLE_MAX_BYTES:-0}"
    echo "external_required_bytes=$REQUIRED_EXTERNAL_BYTES"
    echo "system_super_policy=never-use-generic-recovery-installer"
    echo "boot_policy=keep-normal-boot-test-in-recovery-until-rootfs-proven"
    echo "persistent_phone_writes=no"
    echo "block_writes=no"
    echo "mount_operations=no"
    echo "decision=$DECISION"
} | tee "$OUT/summary.txt"

tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "A33 rootfs storage capacity v3 audit collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Checksum:  $ARCHIVE.sha256"
echo "Upload the .tar.gz archive only."

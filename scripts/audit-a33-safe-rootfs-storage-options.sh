#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
PMBOOTSTRAP_WORK="${PMBOOTSTRAP_WORK:-$HOME/.local/var/pmbootstrap}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-normal-rootfs}"
ADB="${ADB:-adb}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$PORT_ROOT/build/a33-rootfs-storage-options-audit-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
MIN_EXTERNAL_BYTES=$((4 * 1024 * 1024 * 1024))
CACHE_MARGIN_BYTES=$((128 * 1024 * 1024))

for command in "$ADB" sha256sum stat file find awk grep sed tar readlink blkid dumpe2fs resize2fs du; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

mkdir -p "$OUT"/{host,twrp,images}

capture() {
    local output="$1"
    shift
    {
        printf 'command:'
        printf ' %q' "$@"
        printf '\n\n'
        "$@"
    } > "$output" 2>&1 || true
}

{
    echo "created=$(date -Ins)"
    echo "audit_version=1"
    echo "collection_policy=read-only"
    echo "phone_required_mode=TWRP"
    echo "persistent_phone_writes=no"
    echo "block_writes=no"
    echo "mount_operations=no"
    echo "directory_content_listing_policy=no-userdata-content-listing"
    echo "external_preferred_minimum_bytes=$MIN_EXTERNAL_BYTES"
    echo "cache_required_free_margin_bytes=$CACHE_MARGIN_BYTES"
} | tee "$OUT/manifest.txt"

ROOT_LINK="$EXPORT_DIR/samsung-a33x-root.img"
BOOT_LINK="$EXPORT_DIR/samsung-a33x-boot.img"
COMBINED_LINK="$EXPORT_DIR/samsung-a33x.img"
ZIP_LINK="$EXPORT_DIR/pmos-samsung-a33x.zip"

resolve_file() {
    local path="$1"
    local resolved
    resolved="$(readlink -f "$path" 2>/dev/null || true)"
    if [[ -n "$resolved" && -f "$resolved" ]]; then
        printf '%s\n' "$resolved"
    fi
}

ROOT_IMAGE="$(resolve_file "$ROOT_LINK")"
BOOT_IMAGE="$(resolve_file "$BOOT_LINK")"
COMBINED_IMAGE="$(resolve_file "$COMBINED_LINK")"
ZIP="$(resolve_file "$ZIP_LINK")"

{
    echo "root_image_link=$ROOT_LINK"
    echo "root_image=${ROOT_IMAGE:-missing}"
    echo "boot_image_link=$BOOT_LINK"
    echo "boot_image=${BOOT_IMAGE:-missing}"
    echo "combined_image_link=$COMBINED_LINK"
    echo "combined_image=${COMBINED_IMAGE:-missing}"
    echo "installer_link=$ZIP_LINK"
    echo "installer=${ZIP:-missing}"
} > "$OUT/host/export-artifacts.txt"

inspect_image() {
    local label="$1"
    local image="$2"
    local output="$OUT/images/$label.txt"

    if [[ -z "$image" || ! -f "$image" ]]; then
        echo "status=missing" > "$output"
        return
    fi

    {
        echo "status=present"
        echo "path=$image"
        stat -Lc 'apparent_size=%s allocated_blocks_512=%b allocated_bytes=%b*512 mode=%a' "$image"
        echo "allocated_bytes=$(du -B1 "$image" | awk 'NR==1 {print $1}')"
        echo "sha256=$(sha256sum "$image" | awk '{print $1}')"
        echo "file=$(file -b "$image")"
        echo "blkid_begin"
        blkid -p "$image" 2>&1 || true
        echo "blkid_end"
    } > "$output"
}

inspect_image root "$ROOT_IMAGE"
inspect_image boot "$BOOT_IMAGE"
inspect_image combined "$COMBINED_IMAGE"

ROOT_APPARENT_BYTES=""
ROOT_USED_BYTES=""
ROOT_MINIMUM_BYTES=""
ROOT_BLOCK_SIZE=""
ROOT_BLOCK_COUNT=""
ROOT_FREE_BLOCKS=""

if [[ -n "$ROOT_IMAGE" && -f "$ROOT_IMAGE" ]]; then
    ROOT_APPARENT_BYTES="$(stat -Lc '%s' "$ROOT_IMAGE")"
    ROOT_BLOCK_SIZE="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block size/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
    ROOT_BLOCK_COUNT="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block count/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
    ROOT_FREE_BLOCKS="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Free blocks/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
    ROOT_MIN_BLOCKS="$(resize2fs -P "$ROOT_IMAGE" 2>&1 | sed -n 's/.*minimum size of the filesystem is \([0-9][0-9]*\).*/\1/p' | tail -n 1)"

    if [[ "$ROOT_BLOCK_SIZE" =~ ^[0-9]+$ && "$ROOT_BLOCK_COUNT" =~ ^[0-9]+$ && "$ROOT_FREE_BLOCKS" =~ ^[0-9]+$ ]]; then
        ROOT_USED_BYTES=$(( (ROOT_BLOCK_COUNT - ROOT_FREE_BLOCKS) * ROOT_BLOCK_SIZE ))
    fi
    if [[ "$ROOT_BLOCK_SIZE" =~ ^[0-9]+$ && "$ROOT_MIN_BLOCKS" =~ ^[0-9]+$ ]]; then
        ROOT_MINIMUM_BYTES=$(( ROOT_MIN_BLOCKS * ROOT_BLOCK_SIZE ))
    fi

    {
        echo "root_image=$ROOT_IMAGE"
        echo "root_apparent_bytes=${ROOT_APPARENT_BYTES:-unknown}"
        echo "root_block_size=${ROOT_BLOCK_SIZE:-unknown}"
        echo "root_block_count=${ROOT_BLOCK_COUNT:-unknown}"
        echo "root_free_blocks=${ROOT_FREE_BLOCKS:-unknown}"
        echo "root_used_bytes=${ROOT_USED_BYTES:-unknown}"
        echo "root_minimum_bytes=${ROOT_MINIMUM_BYTES:-unknown}"
        echo "dumpe2fs_header_begin"
        dumpe2fs -h "$ROOT_IMAGE" 2>&1 || true
        echo "dumpe2fs_header_end"
        echo "resize2fs_minimum_begin"
        resize2fs -P "$ROOT_IMAGE" 2>&1 || true
        echo "resize2fs_minimum_end"
    } > "$OUT/images/root-filesystem-capacity.txt"
else
    echo "root_image_status=missing" > "$OUT/images/root-filesystem-capacity.txt"
fi

if [[ -n "$ZIP" && -f "$ZIP" ]]; then
    {
        echo "installer=$ZIP"
        stat -Lc 'installer_size=%s' "$ZIP"
        sha256sum "$ZIP"
        unzip -l "$ZIP" 2>/dev/null | tail -n 30 || true
    } > "$OUT/host/installer-size.txt"
fi

echo "=== Wait for exact known-good TWRP ADB shell ==="
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

"$ADB" shell sh -s > "$OUT/twrp/storage-topology.txt" <<'SH'
set -u

safe_link() {
    readlink -f "$1" 2>/dev/null || true
}

block_bytes() {
    blockdev --getsize64 "$1" 2>/dev/null || true
}

mounted_at() {
    awk -v target="$1" '$2==target {print $1; exit}' /proc/mounts 2>/dev/null || true
}

echo "mode=TWRP"
echo "kernel=$(uname -r 2>/dev/null || true)"
echo "recovery_hash=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"

echo "=== encryption state (no secrets) ==="
for prop in \
    ro.crypto.state \
    ro.crypto.type \
    ro.crypto.volume.filenames_mode \
    ro.crypto.metadata.enabled \
    twrp.decrypt.done \
    twrp.decrypt.password; do
    case "$prop" in
        twrp.decrypt.password)
            value="$(getprop "$prop" 2>/dev/null || true)"
            [ -n "$value" ] && echo "$prop=<present-redacted>" || echo "$prop=<absent>"
            ;;
        *) echo "$prop=$(getprop "$prop" 2>/dev/null || true)" ;;
    esac
done

echo "=== persistent internal candidate partitions ==="
for name in cache userdata super boot recovery metadata; do
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

echo "=== mount state and free capacity ==="
cat /proc/mounts
for mountpoint in /cache /data /sdcard /external_sd /storage /mnt/media_rw; do
    source="$(mounted_at "$mountpoint")"
    echo "mountpoint_begin=$mountpoint"
    echo "mount_source=${source:-none}"
    if [ -n "$source" ]; then
        df -B1 "$mountpoint" 2>&1 | tail -n 1 | sed 's/^/df=/' || true
    fi
    echo "mountpoint_end=$mountpoint"
done

echo "=== block inventory with removable flag ==="
for sys in /sys/class/block/*; do
    [ -e "$sys" ] || continue
    base="${sys##*/}"
    case "$base" in
        loop*|ram*|zram*) continue ;;
    esac
    dev="/dev/block/$base"
    [ -b "$dev" ] || dev="/dev/$base"
    size_sectors="$(cat "$sys/size" 2>/dev/null || true)"
    removable="$(cat "$sys/removable" 2>/dev/null || true)"
    logical_block="$(cat "$sys/queue/logical_block_size" 2>/dev/null || true)"
    bytes=""
    if [ -n "$size_sectors" ]; then
        bytes=$((size_sectors * 512))
    fi
    echo "block_begin=$base"
    echo "block_dev=$dev"
    echo "block_bytes=${bytes:-unknown}"
    echo "block_removable=${removable:-unknown}"
    echo "block_logical_block_size=${logical_block:-unknown}"
    echo "block_partition=$(cat "$sys/partition" 2>/dev/null || echo no)"
    echo "block_parent_slaves=$(find "$sys/slaves" -mindepth 1 -maxdepth 1 -printf '%f ' 2>/dev/null || true)"
    echo "block_blkid=$(blkid "$dev" 2>/dev/null | tr '\n\r' '  ')"
    echo "block_end=$base"
done

echo "=== removable storage candidates ==="
for sys in /sys/class/block/*; do
    [ -e "$sys" ] || continue
    removable="$(cat "$sys/removable" 2>/dev/null || true)"
    partition="$(cat "$sys/partition" 2>/dev/null || true)"
    [ "$removable" = 1 ] || continue
    [ -z "$partition" ] || continue
    base="${sys##*/}"
    sectors="$(cat "$sys/size" 2>/dev/null || true)"
    bytes=""
    [ -n "$sectors" ] && bytes=$((sectors * 512))
    echo "removable_device=$base bytes=${bytes:-unknown}"
done

echo "=== privacy note ==="
echo "userdata_directory_contents=not-listed"
echo "wifi_credentials=not-read"
echo "ssh_keys=not-read"
SH

TOPOLOGY="$OUT/twrp/storage-topology.txt"
value_for_candidate() {
    local candidate="$1"
    local key="$2"
    awk -F= -v candidate="$candidate" -v key="$key" '
        $0=="candidate_begin=" candidate {inside=1; next}
        $0=="candidate_end=" candidate {inside=0}
        inside && $1==key {print substr($0, length(key)+2); exit}
    ' "$TOPOLOGY"
}

CACHE_BYTES="$(value_for_candidate cache candidate_bytes)"
USERDATA_BYTES="$(value_for_candidate userdata candidate_bytes)"
CACHE_MOUNT_SOURCE="$(awk -F= '$1=="mountpoint_begin" && $2=="/cache" {inside=1; next} inside && $1=="mount_source" {print $2; exit}' "$TOPOLOGY")"
DATA_MOUNT_SOURCE="$(awk -F= '$1=="mountpoint_begin" && $2=="/data" {inside=1; next} inside && $1=="mount_source" {print $2; exit}' "$TOPOLOGY")"
CACHE_FREE_BYTES="$(awk -F= '$1=="mountpoint_begin" && $2=="/cache" {inside=1; next} inside && $1=="df" {split($2,a,/ +/); print a[4]; exit}' "$TOPOLOGY")"
DATA_FREE_BYTES="$(awk -F= '$1=="mountpoint_begin" && $2=="/data" {inside=1; next} inside && $1=="df" {split($2,a,/ +/); print a[4]; exit}' "$TOPOLOGY")"
CRYPTO_STATE="$(awk -F= '$1=="ro.crypto.state" {print $2; exit}' "$TOPOLOGY")"
CRYPTO_TYPE="$(awk -F= '$1=="ro.crypto.type" {print $2; exit}' "$TOPOLOGY")"
REMOVABLE_MAX_BYTES="$(awk '
    /^removable_device=/ {
        for (i=1; i<=NF; ++i) if ($i ~ /^bytes=/) {
            split($i,a,"="); if (a[2]+0 > max) max=a[2]+0
        }
    }
    END {if (max) printf "%.0f\n", max}
' "$TOPOLOGY")"
REMOVABLE_COUNT="$(grep -c '^removable_device=' "$TOPOLOGY" || true)"

CACHE_REQUIRED_BYTES=""
if [[ "$ROOT_MINIMUM_BYTES" =~ ^[0-9]+$ ]]; then
    CACHE_REQUIRED_BYTES=$(( ROOT_MINIMUM_BYTES + CACHE_MARGIN_BYTES ))
fi

CACHE_CAPACITY_RESULT="unknown"
if [[ "$CACHE_BYTES" =~ ^[0-9]+$ && "$CACHE_REQUIRED_BYTES" =~ ^[0-9]+$ ]]; then
    if (( CACHE_BYTES >= CACHE_REQUIRED_BYTES )); then
        CACHE_CAPACITY_RESULT="fits-minimum-with-128MiB-margin"
    else
        CACHE_CAPACITY_RESULT="too-small-for-minimum-plus-margin"
    fi
fi

DATA_FILE_RESULT="not-approved-early-boot-fbe-access-unproven"
if [[ -n "$DATA_MOUNT_SOURCE" && "$DATA_MOUNT_SOURCE" != none ]]; then
    DATA_FILE_RESULT="twrp-can-access-but-linux-initramfs-fbe-access-unproven"
fi

DECISION="no-safe-rootfs-target-confirmed"
if [[ "$REMOVABLE_MAX_BYTES" =~ ^[0-9]+$ && "$ROOT_APPARENT_BYTES" =~ ^[0-9]+$ ]]; then
    required_external="$MIN_EXTERNAL_BYTES"
    if (( ROOT_APPARENT_BYTES * 2 > required_external )); then
        required_external=$(( ROOT_APPARENT_BYTES * 2 ))
    fi
    if (( REMOVABLE_MAX_BYTES >= required_external )); then
        DECISION="external-removable-rootfs-preferred"
    fi
fi
if [[ "$DECISION" == no-safe-rootfs-target-confirmed && "$CACHE_CAPACITY_RESULT" == fits-minimum-with-128MiB-margin ]]; then
    DECISION="cache-may-fit-minimal-rootfs-but-requires-dedicated-controlled-image"
fi

{
    echo "audit_version=1"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "root_image=${ROOT_IMAGE:-missing}"
    echo "root_apparent_bytes=${ROOT_APPARENT_BYTES:-unknown}"
    echo "root_used_bytes=${ROOT_USED_BYTES:-unknown}"
    echo "root_minimum_bytes=${ROOT_MINIMUM_BYTES:-unknown}"
    echo "cache_bytes=${CACHE_BYTES:-unknown}"
    echo "cache_free_bytes=${CACHE_FREE_BYTES:-unknown}"
    echo "cache_required_bytes=${CACHE_REQUIRED_BYTES:-unknown}"
    echo "cache_capacity_result=$CACHE_CAPACITY_RESULT"
    echo "userdata_bytes=${USERDATA_BYTES:-unknown}"
    echo "userdata_free_bytes=${DATA_FREE_BYTES:-unknown}"
    echo "userdata_twrp_mount_source=${DATA_MOUNT_SOURCE:-none}"
    echo "userdata_crypto_state=${CRYPTO_STATE:-unknown}"
    echo "userdata_crypto_type=${CRYPTO_TYPE:-unknown}"
    echo "userdata_rootfs_file_result=$DATA_FILE_RESULT"
    echo "removable_device_count=$REMOVABLE_COUNT"
    echo "removable_max_bytes=${REMOVABLE_MAX_BYTES:-0}"
    echo "system_super_policy=never-use-generic-recovery-installer"
    echo "boot_policy=keep-normal-boot-test-in-recovery-until-rootfs-proven"
    echo "persistent_phone_writes=no"
    echo "decision=$DECISION"
} | tee "$OUT/summary.txt"

tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "A33 rootfs storage options audit collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Checksum:  $ARCHIVE.sha256"
echo "Upload the .tar.gz archive only."

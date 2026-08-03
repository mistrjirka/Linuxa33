#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
IMAGE_LINK="${IMAGE_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img}"
IMAGE_MANIFEST_LINK="${IMAGE_MANIFEST_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt}"
BACKUP_ROOT="${BACKUP_ROOT:-$PORT_ROOT/build/private-backups}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
PRIVATE_DIR="$BACKUP_ROOT/a33-before-userdata-repurpose-$TIMESTAMP"
PUBLIC_DIR="$RESULT_ROOT/a33-userdata-preflight-$TIMESTAMP"
PUBLIC_ARCHIVE="$PUBLIC_DIR.tar.gz"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA_RESOLVED="${EXPECTED_USERDATA_RESOLVED:-/dev/block/sda36}"
EXPECTED_USERDATA_BYTES="${EXPECTED_USERDATA_BYTES:-114240258048}"
DISK_DEVICE="${DISK_DEVICE:-/dev/block/sda}"
EDGE_BYTES=$((4 * 1024 * 1024))
USERDATA_SAMPLE_BYTES=$((16 * 1024 * 1024))
MAX_PARTITION_BACKUP_BYTES=$((512 * 1024 * 1024))

for command in "$ADB" readlink sha256sum stat awk grep sed find sort tar chmod mkdir date; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

IMAGE="$(readlink -f "$IMAGE_LINK" 2>/dev/null || true)"
IMAGE_MANIFEST="$(readlink -f "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$IMAGE" || ! -f "$IMAGE" || -z "$IMAGE_MANIFEST" || ! -f "$IMAGE_MANIFEST" ]]; then
    echo "REFUSING: prepared userdata rootfs image or manifest is missing" >&2
    echo "Run scripts/prepare-a33-userdata-rootfs-image.sh first." >&2
    exit 1
fi

manifest_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$IMAGE_MANIFEST"
}

if [[ "$(manifest_value preparation_status)" != passed ]]; then
    echo "REFUSING: userdata rootfs image is not marked preparation_status=passed" >&2
    exit 1
fi
IMAGE_EXPECTED_SHA="$(manifest_value deployment_sha256)"
IMAGE_EXPECTED_SIZE="$(manifest_value deployment_size)"
IMAGE_ACTUAL_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_ACTUAL_SIZE="$(stat -Lc '%s' "$IMAGE")"
if [[ "$IMAGE_ACTUAL_SHA" != "$IMAGE_EXPECTED_SHA" || "$IMAGE_ACTUAL_SIZE" != "$IMAGE_EXPECTED_SIZE" ]]; then
    echo "REFUSING: userdata rootfs image differs from its manifest" >&2
    exit 1
fi

mkdir -p "$PRIVATE_DIR" "$PUBLIC_DIR"
chmod 700 "$BACKUP_ROOT" "$PRIVATE_DIR"
chmod 755 "$RESULT_ROOT" "$PUBLIC_DIR"

cat > "$PRIVATE_DIR/README-PRIVATE.txt" <<'EOF'
PRIVATE LOCAL RESCUE MATERIAL

This directory may contain Android encryption metadata and raw boot-chain data.
Do not upload it, commit it, or share it. It is not a complete Android userdata
backup and cannot recover deleted user files by itself.
EOF
chmod 600 "$PRIVATE_DIR/README-PRIVATE.txt"

echo "=== Wait for exact known-good TWRP ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

remote_value() {
    "$ADB" shell "$1" 2>/dev/null | tr -d '\r' | head -n 1
}

RECOVERY_SHA="$(remote_value 'sha256sum /dev/block/by-name/recovery | awk "NR==1 {print \$1}"')"
if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running exact known-good TWRP" >&2
    echo "expected=$KNOWN_TWRP_SHA256" >&2
    echo "actual=${RECOVERY_SHA:-missing}" >&2
    exit 1
fi

USERDATA_RESOLVED="$(remote_value 'readlink -f /dev/block/by-name/userdata')"
USERDATA_BYTES="$(remote_value 'blockdev --getsize64 /dev/block/by-name/userdata')"
DISK_BYTES="$(remote_value "blockdev --getsize64 $DISK_DEVICE")"
DATA_MOUNT_SOURCE="$(remote_value "awk '\$2==\"/data\" {print \$1; exit}' /proc/mounts")"
USERDATA_DM_USERS="$(
    "$ADB" shell sh -s 2>/dev/null <<'SH' | tr -d '\r'
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    if find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -qx sda36; then
        echo "${dm##*/}:$(cat "$dm/dm/name" 2>/dev/null || true)"
    fi
done
SH
)"

if [[ "$USERDATA_RESOLVED" != "$EXPECTED_USERDATA_RESOLVED" ]]; then
    echo "REFUSING: userdata resolves to an unexpected block device" >&2
    echo "expected=$EXPECTED_USERDATA_RESOLVED actual=$USERDATA_RESOLVED" >&2
    exit 1
fi
if [[ "$USERDATA_BYTES" != "$EXPECTED_USERDATA_BYTES" ]]; then
    echo "REFUSING: userdata size changed" >&2
    echo "expected=$EXPECTED_USERDATA_BYTES actual=$USERDATA_BYTES" >&2
    exit 1
fi
if [[ ! "$DISK_BYTES" =~ ^[0-9]+$ || "$DISK_BYTES" -le "$USERDATA_BYTES" ]]; then
    echo "REFUSING: invalid UFS disk size: ${DISK_BYTES:-missing}" >&2
    exit 1
fi
if [[ -n "$DATA_MOUNT_SOURCE" ]]; then
    echo "REFUSING: /data is mounted in TWRP: $DATA_MOUNT_SOURCE" >&2
    exit 1
fi
if [[ -n "$USERDATA_DM_USERS" ]]; then
    echo "REFUSING: userdata is currently used by device-mapper" >&2
    echo "$USERDATA_DM_USERS" >&2
    exit 1
fi
if (( IMAGE_ACTUAL_SIZE >= USERDATA_BYTES )); then
    echo "REFUSING: root image does not fit userdata" >&2
    exit 1
fi

"$ADB" shell sh -s > "$PRIVATE_DIR/phone-topology.txt" <<'SH'
set -u
echo "=== by-name ==="
ls -la /dev/block/by-name 2>&1 || true
echo "=== proc partitions ==="
cat /proc/partitions 2>&1 || true
echo "=== mounts ==="
cat /proc/mounts 2>&1 || true
echo "=== fdisk ==="
fdisk -l /dev/block/sda 2>&1 || true
echo "=== block sizes ==="
for path in /dev/block/by-name/*; do
    [ -b "$path" ] || continue
    echo "$path -> $(readlink -f "$path" 2>/dev/null || true) bytes=$(blockdev --getsize64 "$path" 2>/dev/null || true)"
done
SH
chmod 600 "$PRIVATE_DIR/phone-topology.txt"

pull_exact() {
    local remote="$1" output="$2" expected="$3"
    echo "Backing up $remote -> $output"
    "$ADB" exec-out sh -c "dd if='$remote' bs=1048576 2>/dev/null" > "$output"
    chmod 600 "$output"
    local actual
    actual="$(stat -Lc '%s' "$output")"
    if [[ "$actual" != "$expected" ]]; then
        echo "REFUSING: backup size mismatch for $remote" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
    sha256sum "$output" >> "$PRIVATE_DIR/SHA256SUMS"
}

pull_range_4k() {
    local remote="$1" output="$2" skip="$3" count="$4" expected="$5"
    echo "Backing up range $remote skip=$skip count=$count -> $output"
    "$ADB" exec-out sh -c "dd if='$remote' bs=4096 skip='$skip' count='$count' 2>/dev/null" > "$output"
    chmod 600 "$output"
    local actual
    actual="$(stat -Lc '%s' "$output")"
    if [[ "$actual" != "$expected" ]]; then
        echo "REFUSING: ranged backup size mismatch for $remote" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
    sha256sum "$output" >> "$PRIVATE_DIR/SHA256SUMS"
}

: > "$PRIVATE_DIR/SHA256SUMS"
chmod 600 "$PRIVATE_DIR/SHA256SUMS"

DISK_BLOCKS=$((DISK_BYTES / 4096))
EDGE_BLOCKS=$((EDGE_BYTES / 4096))
USERDATA_BLOCKS=$((USERDATA_BYTES / 4096))
USERDATA_SAMPLE_BLOCKS=$((USERDATA_SAMPLE_BYTES / 4096))

pull_range_4k "$DISK_DEVICE" "$PRIVATE_DIR/ufs-gpt-primary-and-prefix.bin" 0 "$EDGE_BLOCKS" "$EDGE_BYTES"
pull_range_4k "$DISK_DEVICE" "$PRIVATE_DIR/ufs-gpt-backup-and-suffix.bin" "$((DISK_BLOCKS - EDGE_BLOCKS))" "$EDGE_BLOCKS" "$EDGE_BYTES"
pull_range_4k /dev/block/by-name/userdata "$PRIVATE_DIR/userdata-first-16MiB.bin" 0 "$USERDATA_SAMPLE_BLOCKS" "$USERDATA_SAMPLE_BYTES"
pull_range_4k /dev/block/by-name/userdata "$PRIVATE_DIR/userdata-last-16MiB.bin" "$((USERDATA_BLOCKS - USERDATA_SAMPLE_BLOCKS))" "$USERDATA_SAMPLE_BLOCKS" "$USERDATA_SAMPLE_BYTES"

BACKUP_NAMES=(
    boot recovery metadata misc dtbo vbmeta vbmeta_system vbmeta_vendor
    vendor_boot init_boot efs sec_efs persist param up_param
)

: > "$PRIVATE_DIR/partition-backups.tsv"
for name in "${BACKUP_NAMES[@]}"; do
    path="/dev/block/by-name/$name"
    size="$(remote_value "if [ -b '$path' ]; then blockdev --getsize64 '$path'; fi")"
    [[ "$size" =~ ^[0-9]+$ ]] || continue
    if (( size <= 0 || size > MAX_PARTITION_BACKUP_BYTES )); then
        printf '%s\t%s\t%s\n' "$name" "$size" skipped-size-limit >> "$PRIVATE_DIR/partition-backups.tsv"
        continue
    fi
    output="$PRIVATE_DIR/partition-$name.img"
    pull_exact "$path" "$output" "$size"
    printf '%s\t%s\t%s\n' "$name" "$size" "$output" >> "$PRIVATE_DIR/partition-backups.tsv"
done
chmod 600 "$PRIVATE_DIR/partition-backups.tsv"

PRIVATE_MANIFEST="$PRIVATE_DIR/manifest.txt"
{
    echo "created=$(date -Ins)"
    echo "privacy=private-do-not-upload"
    echo "backup_status=passed"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "disk_device=$DISK_DEVICE"
    echo "disk_bytes=$DISK_BYTES"
    echo "userdata_path=/dev/block/by-name/userdata"
    echo "userdata_resolved=$USERDATA_RESOLVED"
    echo "userdata_bytes=$USERDATA_BYTES"
    echo "userdata_mounted=no"
    echo "userdata_device_mapper_users=none"
    echo "deployment_image=$IMAGE"
    echo "deployment_sha256=$IMAGE_ACTUAL_SHA"
    echo "deployment_size=$IMAGE_ACTUAL_SIZE"
    echo "android_userdata_full_backup=no"
    echo "android_userdata_loss_expected=yes"
} | tee "$PRIVATE_MANIFEST"
chmod 600 "$PRIVATE_MANIFEST"

PRIVATE_MANIFEST_SHA="$(sha256sum "$PRIVATE_MANIFEST" | awk '{print $1}')"
PRIVATE_SUMS_SHA="$(sha256sum "$PRIVATE_DIR/SHA256SUMS" | awk '{print $1}')"

{
    echo "created=$(date -Ins)"
    echo "audit=userdata-repurpose-preflight"
    echo "phone_writes=no"
    echo "phone_mount_operations=no"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "disk_device=$DISK_DEVICE"
    echo "disk_bytes=$DISK_BYTES"
    echo "userdata_path=/dev/block/by-name/userdata"
    echo "userdata_resolved=$USERDATA_RESOLVED"
    echo "userdata_bytes=$USERDATA_BYTES"
    echo "userdata_mounted=no"
    echo "userdata_device_mapper_users=none"
    echo "deployment_sha256=$IMAGE_ACTUAL_SHA"
    echo "deployment_size=$IMAGE_ACTUAL_SIZE"
    echo "deployment_fits_userdata=yes"
    echo "private_backup_dir=$PRIVATE_DIR"
    echo "private_manifest_sha256=$PRIVATE_MANIFEST_SHA"
    echo "private_sha256sums_sha256=$PRIVATE_SUMS_SHA"
    echo "private_backup_status=passed"
    echo "full_android_userdata_backup=no"
    echo "destructive_next_step_erases_android_userdata=yes"
    echo "preflight_status=passed"
} | tee "$PUBLIC_DIR/summary.txt"

cp "$PUBLIC_DIR/summary.txt" "$PRIVATE_DIR/public-summary-copy.txt"
chmod 600 "$PRIVATE_DIR/public-summary-copy.txt"

tar -C "$RESULT_ROOT" -czf "$PUBLIC_ARCHIVE" "$(basename "$PUBLIC_DIR")"
sha256sum "$PUBLIC_ARCHIVE" | tee "$PUBLIC_ARCHIVE.sha256"

echo
echo "A33 userdata repurpose preflight and private backup completed."
echo "PRIVATE backup (DO NOT UPLOAD): $PRIVATE_DIR"
echo "Sanitized archive to upload:     $PUBLIC_ARCHIVE"
echo "No phone partition was written."

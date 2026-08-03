#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
ROOT_IMAGE_LINK="${ROOT_IMAGE_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img}"
ROOT_MANIFEST_LINK="${ROOT_MANIFEST_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt}"
BOOT_IMAGE_LINK="${BOOT_IMAGE_LINK:-$PORT_ROOT/build/cache-boot-images/current/a33x-cache-pmos-boot.img}"
BOOT_MANIFEST_LINK="${BOOT_MANIFEST_LINK:-$PORT_ROOT/build/cache-boot-images/current/manifest.txt}"
BACKUP_ROOT="${BACKUP_ROOT:-$PORT_ROOT/build/private-backups}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
PUBLIC_DIR="$RESULT_ROOT/a33-internal-layout-preflight-$TIMESTAMP"
PUBLIC_ARCHIVE="$PUBLIC_DIR.tar.gz"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_CACHE_RESOLVED="${EXPECTED_CACHE_RESOLVED:-/dev/block/sda33}"
EXPECTED_CACHE_BYTES="${EXPECTED_CACHE_BYTES:-629145600}"
EXPECTED_USERDATA_RESOLVED="${EXPECTED_USERDATA_RESOLVED:-/dev/block/sda36}"
EXPECTED_USERDATA_BYTES="${EXPECTED_USERDATA_BYTES:-114240258048}"

for command in "$ADB" readlink sha256sum stat awk grep find sort tar chmod mkdir date mv; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

resolve_file() {
    local path="$1" resolved
    resolved="$(readlink -f "$path" 2>/dev/null || true)"
    [[ -n "$resolved" && -f "$resolved" ]] && printf '%s\n' "$resolved"
}

ROOT_IMAGE="$(resolve_file "$ROOT_IMAGE_LINK")"
ROOT_MANIFEST="$(resolve_file "$ROOT_MANIFEST_LINK")"
BOOT_IMAGE="$(resolve_file "$BOOT_IMAGE_LINK")"
BOOT_MANIFEST="$(resolve_file "$BOOT_MANIFEST_LINK")"
for required in "$ROOT_IMAGE" "$ROOT_MANIFEST" "$BOOT_IMAGE" "$BOOT_MANIFEST"; do
    [[ -n "$required" && -f "$required" ]] || {
        echo "REFUSING: prepared root/boot image or manifest is missing" >&2
        echo "Run prepare-a33-userdata-rootfs-image.sh and prepare-a33-cache-boot-image.sh first." >&2
        exit 1
    }
done

manifest_value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

if [[ "$(manifest_value "$ROOT_MANIFEST" preparation_status)" != passed || \
      "$(manifest_value "$BOOT_MANIFEST" preparation_status)" != passed ]]; then
    echo "REFUSING: root or boot image is not marked preparation_status=passed" >&2
    exit 1
fi
ROOT_SHA="$(sha256sum "$ROOT_IMAGE" | awk '{print $1}')"
ROOT_SIZE="$(stat -Lc '%s' "$ROOT_IMAGE")"
BOOT_SHA="$(sha256sum "$BOOT_IMAGE" | awk '{print $1}')"
BOOT_SIZE="$(stat -Lc '%s' "$BOOT_IMAGE")"
if [[ "$ROOT_SHA" != "$(manifest_value "$ROOT_MANIFEST" deployment_sha256)" || \
      "$ROOT_SIZE" != "$(manifest_value "$ROOT_MANIFEST" deployment_size)" ]]; then
    echo "REFUSING: prepared root image differs from its manifest" >&2
    exit 1
fi
if [[ "$BOOT_SHA" != "$(manifest_value "$BOOT_MANIFEST" boot_sha256)" || \
      "$BOOT_SIZE" != "$(manifest_value "$BOOT_MANIFEST" boot_size)" ]]; then
    echo "REFUSING: prepared boot image differs from its manifest" >&2
    exit 1
fi
if (( BOOT_SIZE >= EXPECTED_CACHE_BYTES || ROOT_SIZE >= EXPECTED_USERDATA_BYTES )); then
    echo "REFUSING: prepared image does not fit its target" >&2
    exit 1
fi

PRIVATE_DIR="$(
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'a33-before-userdata-repurpose-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
PRIVATE_MANIFEST="$PRIVATE_DIR/manifest.txt"
PRIVATE_SUMS="$PRIVATE_DIR/SHA256SUMS"
if [[ -z "$PRIVATE_DIR" || ! -f "$PRIVATE_MANIFEST" || ! -f "$PRIVATE_SUMS" ]]; then
    echo "REFUSING: prior private userdata preflight backup is missing" >&2
    echo "Run scripts/backup-a33-before-userdata-repurpose.sh first." >&2
    exit 1
fi
if [[ "$(manifest_value "$PRIVATE_MANIFEST" backup_status)" != passed || \
      "$(manifest_value "$PRIVATE_MANIFEST" deployment_sha256)" != "$ROOT_SHA" ]]; then
    echo "REFUSING: prior private backup does not match the prepared root image" >&2
    exit 1
fi

mkdir -p "$PUBLIC_DIR"
chmod 755 "$PUBLIC_DIR"
chmod 700 "$PRIVATE_DIR"

echo "=== Wait for exact known-good TWRP ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

remote_value() {
    "$ADB" shell "$1" 2>/dev/null | tr -d '\r' | head -n 1
}

RECOVERY_SHA="$(remote_value 'sha256sum /dev/block/by-name/recovery | awk "NR==1 {print \$1}"')"
CACHE_RESOLVED="$(remote_value 'readlink -f /dev/block/by-name/cache')"
CACHE_BYTES="$(remote_value 'blockdev --getsize64 /dev/block/by-name/cache')"
USERDATA_RESOLVED="$(remote_value 'readlink -f /dev/block/by-name/userdata')"
USERDATA_BYTES="$(remote_value 'blockdev --getsize64 /dev/block/by-name/userdata')"
CACHE_MOUNT_SOURCE="$(remote_value "awk '\$2==\"/cache\" {print \$1; exit}' /proc/mounts")"
DATA_MOUNT_SOURCE="$(remote_value "awk '\$2==\"/data\" {print \$1; exit}' /proc/mounts")"
DM_USERS="$(
    "$ADB" shell sh -s 2>/dev/null <<'SH' | tr -d '\r'
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    slaves="$(find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null || true)"
    if printf '%s\n' "$slaves" | grep -Eq '^(sda33|sda36)$'; then
        echo "${dm##*/}:$(cat "$dm/dm/name" 2>/dev/null || true):$(printf '%s' "$slaves" | tr '\n' ',')"
    fi
done
SH
)"

if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running exact known-good TWRP" >&2
    exit 1
fi
if [[ "$CACHE_RESOLVED" != "$EXPECTED_CACHE_RESOLVED" || "$CACHE_BYTES" != "$EXPECTED_CACHE_BYTES" ]]; then
    echo "REFUSING: cache identity changed" >&2
    echo "resolved=$CACHE_RESOLVED bytes=$CACHE_BYTES" >&2
    exit 1
fi
if [[ "$USERDATA_RESOLVED" != "$EXPECTED_USERDATA_RESOLVED" || "$USERDATA_BYTES" != "$EXPECTED_USERDATA_BYTES" ]]; then
    echo "REFUSING: userdata identity changed" >&2
    exit 1
fi
if [[ -n "$CACHE_MOUNT_SOURCE" || -n "$DATA_MOUNT_SOURCE" || -n "$DM_USERS" ]]; then
    echo "REFUSING: cache or userdata is mounted/in use" >&2
    echo "cache_mount=${CACHE_MOUNT_SOURCE:-none}" >&2
    echo "data_mount=${DATA_MOUNT_SOURCE:-none}" >&2
    echo "dm_users=${DM_USERS:-none}" >&2
    exit 1
fi

CACHE_BACKUP="$PRIVATE_DIR/partition-cache.img"
CACHE_BACKUP_TMP="$CACHE_BACKUP.partial"
rm -f "$CACHE_BACKUP_TMP"
echo "=== Back up complete Android cache partition privately ==="
"$ADB" exec-out sh -c "dd if=/dev/block/by-name/cache bs=1048576 2>/dev/null" > "$CACHE_BACKUP_TMP"
if [[ "$(stat -Lc '%s' "$CACHE_BACKUP_TMP")" != "$CACHE_BYTES" ]]; then
    echo "REFUSING: cache backup size mismatch" >&2
    rm -f "$CACHE_BACKUP_TMP"
    exit 1
fi
chmod 600 "$CACHE_BACKUP_TMP"
mv "$CACHE_BACKUP_TMP" "$CACHE_BACKUP"
CACHE_BACKUP_SHA="$(sha256sum "$CACHE_BACKUP" | awk '{print $1}')"
# Replace any prior cache entry, then append the current one.
grep -vF "  $CACHE_BACKUP" "$PRIVATE_SUMS" > "$PRIVATE_SUMS.tmp" || true
printf '%s  %s\n' "$CACHE_BACKUP_SHA" "$CACHE_BACKUP" >> "$PRIVATE_SUMS.tmp"
mv "$PRIVATE_SUMS.tmp" "$PRIVATE_SUMS"
chmod 600 "$PRIVATE_SUMS"

cat >> "$PRIVATE_MANIFEST" <<EOF
cache_path=/dev/block/by-name/cache
cache_resolved=$CACHE_RESOLVED
cache_bytes=$CACHE_BYTES
cache_mounted=no
cache_device_mapper_users=none
cache_backup=$CACHE_BACKUP
cache_backup_sha256=$CACHE_BACKUP_SHA
cache_backup_status=passed
boot_deployment_image=$BOOT_IMAGE
boot_deployment_sha256=$BOOT_SHA
boot_deployment_size=$BOOT_SIZE
internal_layout_preflight_status=passed
EOF
chmod 600 "$PRIVATE_MANIFEST"

PRIVATE_MANIFEST_SHA="$(sha256sum "$PRIVATE_MANIFEST" | awk '{print $1}')"
PRIVATE_SUMS_SHA="$(sha256sum "$PRIVATE_SUMS" | awk '{print $1}')"

{
    echo "created=$(date -Ins)"
    echo "audit=cache-plus-userdata-internal-layout-preflight"
    echo "phone_writes=no"
    echo "phone_mount_operations=no"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "cache_path=/dev/block/by-name/cache"
    echo "cache_resolved=$CACHE_RESOLVED"
    echo "cache_bytes=$CACHE_BYTES"
    echo "cache_mounted=no"
    echo "cache_device_mapper_users=none"
    echo "boot_deployment_sha256=$BOOT_SHA"
    echo "boot_deployment_size=$BOOT_SIZE"
    echo "boot_fits_cache=yes"
    echo "userdata_path=/dev/block/by-name/userdata"
    echo "userdata_resolved=$USERDATA_RESOLVED"
    echo "userdata_bytes=$USERDATA_BYTES"
    echo "userdata_mounted=no"
    echo "userdata_device_mapper_users=none"
    echo "root_deployment_sha256=$ROOT_SHA"
    echo "root_deployment_size=$ROOT_SIZE"
    echo "root_fits_userdata=yes"
    echo "private_backup_dir=$PRIVATE_DIR"
    echo "private_manifest_sha256=$PRIVATE_MANIFEST_SHA"
    echo "private_sha256sums_sha256=$PRIVATE_SUMS_SHA"
    echo "cache_full_backup_status=passed"
    echo "prior_userdata_preflight_status=passed"
    echo "destructive_next_step_erases_android_cache_and_userdata=yes"
    echo "preflight_status=passed"
} | tee "$PUBLIC_DIR/summary.txt"

tar -C "$RESULT_ROOT" -czf "$PUBLIC_ARCHIVE" "$(basename "$PUBLIC_DIR")"
sha256sum "$PUBLIC_ARCHIVE" | tee "$PUBLIC_ARCHIVE.sha256"

echo
echo "A33 internal cache+userdata preflight completed."
echo "PRIVATE backup (DO NOT UPLOAD): $PRIVATE_DIR"
echo "Sanitized archive to upload:     $PUBLIC_ARCHIVE"
echo "No phone partition was written."

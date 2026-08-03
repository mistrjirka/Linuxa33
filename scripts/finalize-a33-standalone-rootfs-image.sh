#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$PORT_ROOT/build/rootfs-images}"
ARTIFACT_DIR="${ARTIFACT_DIR:-}"
CURRENT_LINK="$ARTIFACT_ROOT/current"
REPORT="$PORT_ROOT/build/a33-standalone-rootfs-image.txt"

for command in find sort awk grep sed tail readlink stat sha256sum blkid dumpe2fs resize2fs e2fsck debugfs du; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

if [[ -z "$ARTIFACT_DIR" ]]; then
    ARTIFACT_DIR="$(
        find "$ARTIFACT_ROOT" -mindepth 1 -maxdepth 1 -type d \
            -name '20????????-??????' -printf '%T@ %p\n' 2>/dev/null \
        | sort -nr \
        | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
    )"
fi

if [[ -z "$ARTIFACT_DIR" || ! -d "$ARTIFACT_DIR" ]]; then
    echo "REFUSING: no extracted rootfs artifact directory was found" >&2
    exit 1
fi

ROOT_IMAGE="$ARTIFACT_DIR/samsung-a33x-root.img"
BOOT_IMAGE="$ARTIFACT_DIR/samsung-a33x-boot.img"
for required in "$ROOT_IMAGE" "$BOOT_IMAGE"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: extracted artifact is missing: $required" >&2
        exit 1
    }
done

ROOT_TYPE="$(blkid -p -s TYPE -o value "$ROOT_IMAGE" 2>/dev/null || true)"
if [[ "$ROOT_TYPE" != ext4 ]]; then
    echo "REFUSING: extracted root image is not ext4" >&2
    echo "path=$ROOT_IMAGE type=${ROOT_TYPE:-unknown}" >&2
    exit 1
fi

E2FSCK_RC=0
e2fsck -fn "$ROOT_IMAGE" > "$ARTIFACT_DIR/root-e2fsck-read-only.txt" 2>&1 || E2FSCK_RC=$?
if [[ "$E2FSCK_RC" -ne 0 ]]; then
    echo "REFUSING: read-only e2fsck returned rc=$E2FSCK_RC" >&2
    cat "$ARTIFACT_DIR/root-e2fsck-read-only.txt" >&2
    exit 1
fi

require_ext4_path() {
    local path="$1" label="$2" output
    output="$(debugfs -R "stat $path" "$ROOT_IMAGE" 2>&1 || true)"
    if ! grep -q '^Inode:' <<<"$output"; then
        echo "REFUSING: root image lacks $label at $path" >&2
        echo "$output" >&2
        exit 1
    fi
}

require_ext4_path /sbin/init init
require_ext4_path /usr/sbin/sshd OpenSSH-server
require_ext4_path /etc/runlevels/default/sshd enabled-sshd-service
require_ext4_path /etc/runlevels/default/networkmanager enabled-NetworkManager-service
require_ext4_path /usr/libexec/a33x-muic-switch-dynamic U0g-helper
require_ext4_path /usr/share/mkinitfs/hooks/03-a33x-muic-switch-dynamic.sh U0g-hook03
require_ext4_path /usr/share/mkinitfs/hooks/04-a33x-muic-persist-dynamic.sh U0g-hook04

ROOT_BLOCK_SIZE="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block size/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_BLOCK_COUNT="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block count/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_FREE_BLOCKS="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Free blocks/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"

RESIZE_OUTPUT="$(resize2fs -P "$ROOT_IMAGE" 2>&1)"
printf '%s\n' "$RESIZE_OUTPUT" > "$ARTIFACT_DIR/root-resize2fs-P.txt"
ROOT_MIN_BLOCKS="$(
    printf '%s\n' "$RESIZE_OUTPUT" \
    | awk 'match($0, /[0-9]+[[:space:]]*$/) {value=substr($0, RSTART, RLENGTH); gsub(/[[:space:]]/, "", value)} END {print value}'
)"

for value_name in ROOT_BLOCK_SIZE ROOT_BLOCK_COUNT ROOT_FREE_BLOCKS ROOT_MIN_BLOCKS; do
    value="${!value_name}"
    [[ "$value" =~ ^[0-9]+$ ]] || {
        echo "REFUSING: could not calculate $value_name" >&2
        echo "resize2fs_output=$RESIZE_OUTPUT" >&2
        exit 1
    }
done

ROOT_USED_BYTES=$(( (ROOT_BLOCK_COUNT - ROOT_FREE_BLOCKS) * ROOT_BLOCK_SIZE ))
ROOT_MINIMUM_BYTES=$(( ROOT_MIN_BLOCKS * ROOT_BLOCK_SIZE ))
ROOT_APPARENT_BYTES="$(stat -Lc '%s' "$ROOT_IMAGE")"
ROOT_ALLOCATED_BYTES="$(du -B1 "$ROOT_IMAGE" | awk 'NR==1 {print $1}')"
ROOT_SHA256="$(sha256sum "$ROOT_IMAGE" | awk '{print $1}')"
BOOT_SHA256="$(sha256sum "$BOOT_IMAGE" | awk '{print $1}')"
ROOT_UUID="$(blkid -p -s UUID -o value "$ROOT_IMAGE" 2>/dev/null || true)"
ROOT_LABEL="$(blkid -p -s LABEL -o value "$ROOT_IMAGE" 2>/dev/null || true)"
TIMESTAMP="$(basename "$ARTIFACT_DIR")"

{
    echo "created=$(date -Ins)"
    echo "operation=finalize-already-extracted-standalone-rootfs-image"
    echo "phone_required=no"
    echo "phone_partition_writes=no"
    echo "artifact_dir=$ARTIFACT_DIR"
    echo "root_image=$ROOT_IMAGE"
    echo "root_sha256=$ROOT_SHA256"
    echo "root_apparent_bytes=$ROOT_APPARENT_BYTES"
    echo "root_allocated_bytes=$ROOT_ALLOCATED_BYTES"
    echo "root_used_bytes=$ROOT_USED_BYTES"
    echo "root_minimum_blocks=$ROOT_MIN_BLOCKS"
    echo "root_minimum_bytes=$ROOT_MINIMUM_BYTES"
    echo "root_uuid=${ROOT_UUID:-none}"
    echo "root_label=${ROOT_LABEL:-none}"
    echo "boot_image=$BOOT_IMAGE"
    echo "boot_sha256=$BOOT_SHA256"
    echo "e2fsck_read_only_rc=$E2FSCK_RC"
    echo "u0g_validation=passed"
    echo "openssh_validation=passed"
    echo "networkmanager_validation=passed"
    echo "locale_independent_resize2fs_parse=passed"
    echo "preparation_status=passed"
} | tee "$REPORT" | tee "$ARTIFACT_DIR/manifest.txt"

ln -sfn "$TIMESTAMP" "$CURRENT_LINK"
printf '%s  %s\n' "$ROOT_SHA256" "$ROOT_IMAGE" > "$ROOT_IMAGE.sha256"
printf '%s  %s\n' "$BOOT_SHA256" "$BOOT_IMAGE" > "$BOOT_IMAGE.sha256"

echo
echo "A33 extracted standalone rootfs image finalized."
echo "Root image: $ROOT_IMAGE"
echo "Boot image: $BOOT_IMAGE"
echo "Current:    $CURRENT_LINK"
echo "Report:     $REPORT"
echo "No phone partition was written."

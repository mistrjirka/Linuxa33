#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SOURCE_LINK="${SOURCE_LINK:-$PORT_ROOT/build/rootfs-images/current/samsung-a33x-boot.img}"
SOURCE_MANIFEST_LINK="${SOURCE_MANIFEST_LINK:-$PORT_ROOT/build/rootfs-images/current/manifest.txt}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$PORT_ROOT/build/cache-boot-images}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="$ARTIFACT_ROOT/$TIMESTAMP"
CURRENT_LINK="$ARTIFACT_ROOT/current"
OUT_IMAGE="$ARTIFACT_DIR/a33x-cache-pmos-boot.img"
REPORT="$PORT_ROOT/build/a33-cache-boot-image.txt"
EXPECTED_CACHE_BYTES="${EXPECTED_CACHE_BYTES:-629145600}"

for command in readlink cp sha256sum stat blkid e2fsck debugfs grep awk mkdir chmod date; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

SOURCE_IMAGE="$(readlink -f "$SOURCE_LINK" 2>/dev/null || true)"
SOURCE_MANIFEST="$(readlink -f "$SOURCE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$SOURCE_IMAGE" || ! -f "$SOURCE_IMAGE" ]]; then
    echo "REFUSING: finalized standalone boot image is missing" >&2
    echo "source_link=$SOURCE_LINK" >&2
    exit 1
fi
if [[ -z "$SOURCE_MANIFEST" || ! -f "$SOURCE_MANIFEST" ]]; then
    echo "REFUSING: finalized standalone image manifest is missing" >&2
    exit 1
fi

manifest_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$SOURCE_MANIFEST"
}

if [[ "$(manifest_value preparation_status)" != passed ]]; then
    echo "REFUSING: source image manifest is not preparation_status=passed" >&2
    exit 1
fi
EXPECTED_SOURCE_SHA="$(manifest_value boot_sha256)"
ACTUAL_SOURCE_SHA="$(sha256sum "$SOURCE_IMAGE" | awk '{print $1}')"
if [[ ! "$EXPECTED_SOURCE_SHA" =~ ^[0-9a-f]{64}$ || "$ACTUAL_SOURCE_SHA" != "$EXPECTED_SOURCE_SHA" ]]; then
    echo "REFUSING: source boot image SHA256 mismatch" >&2
    echo "expected=${EXPECTED_SOURCE_SHA:-missing}" >&2
    echo "actual=$ACTUAL_SOURCE_SHA" >&2
    exit 1
fi

SOURCE_SIZE="$(stat -Lc '%s' "$SOURCE_IMAGE")"
if [[ ! "$SOURCE_SIZE" =~ ^[0-9]+$ || "$SOURCE_SIZE" -le 0 || "$SOURCE_SIZE" -ge "$EXPECTED_CACHE_BYTES" ]]; then
    echo "REFUSING: boot image does not fit cache" >&2
    echo "boot_bytes=$SOURCE_SIZE cache_bytes=$EXPECTED_CACHE_BYTES" >&2
    exit 1
fi

SOURCE_TYPE="$(blkid -p -s TYPE -o value "$SOURCE_IMAGE" 2>/dev/null || true)"
SOURCE_LABEL="$(blkid -p -s LABEL -o value "$SOURCE_IMAGE" 2>/dev/null || true)"
SOURCE_UUID="$(blkid -p -s UUID -o value "$SOURCE_IMAGE" 2>/dev/null || true)"
case "$SOURCE_TYPE" in
    ext2|ext3|ext4) ;;
    *) echo "REFUSING: boot image has unsupported filesystem type: ${SOURCE_TYPE:-none}" >&2; exit 1 ;;
esac
if [[ "$SOURCE_LABEL" != pmOS_boot ]]; then
    echo "REFUSING: boot image label is not pmOS_boot" >&2
    echo "actual_label=${SOURCE_LABEL:-none}" >&2
    exit 1
fi
if [[ -z "$SOURCE_UUID" ]]; then
    echo "REFUSING: boot image has no filesystem UUID" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR" "$PORT_ROOT/build"
chmod 700 "$ARTIFACT_DIR"
cp --reflink=auto --sparse=always --dereference "$SOURCE_IMAGE" "$OUT_IMAGE"

E2FSCK_RC=0
e2fsck -fn "$OUT_IMAGE" > "$ARTIFACT_DIR/e2fsck-read-only.txt" 2>&1 || E2FSCK_RC=$?
if [[ "$E2FSCK_RC" -ne 0 ]]; then
    echo "REFUSING: cache boot image failed read-only e2fsck (rc=$E2FSCK_RC)" >&2
    cat "$ARTIFACT_DIR/e2fsck-read-only.txt" >&2
    exit 1
fi

require_path() {
    local path="$1" label="$2" output
    output="$(debugfs -R "stat $path" "$OUT_IMAGE" 2>&1 || true)"
    if ! grep -q '^Inode:' <<<"$output"; then
        echo "REFUSING: boot image lacks $label at $path" >&2
        echo "$output" >&2
        exit 1
    fi
}

# The proven U0g initramfs mounts pmOS_boot first and extracts initramfs-extra
# before it searches for pmOS_root.
require_path /initramfs-extra initramfs-extra
require_path /initramfs initramfs
require_path /samsung-a33x.dtb device-tree

FINAL_SHA="$(sha256sum "$OUT_IMAGE" | awk '{print $1}')"
FINAL_SIZE="$(stat -Lc '%s' "$OUT_IMAGE")"
FINAL_TYPE="$(blkid -p -s TYPE -o value "$OUT_IMAGE" 2>/dev/null || true)"
FINAL_LABEL="$(blkid -p -s LABEL -o value "$OUT_IMAGE" 2>/dev/null || true)"
FINAL_UUID="$(blkid -p -s UUID -o value "$OUT_IMAGE" 2>/dev/null || true)"

{
    echo "created=$(date -Ins)"
    echo "operation=prepare-cache-pmos-boot-image"
    echo "phone_required=no"
    echo "phone_partition_writes=no"
    echo "source_image=$SOURCE_IMAGE"
    echo "source_sha256=$ACTUAL_SOURCE_SHA"
    echo "boot_image=$OUT_IMAGE"
    echo "boot_sha256=$FINAL_SHA"
    echo "boot_size=$FINAL_SIZE"
    echo "boot_type=$FINAL_TYPE"
    echo "boot_label=$FINAL_LABEL"
    echo "boot_uuid=$FINAL_UUID"
    echo "cache_expected_bytes=$EXPECTED_CACHE_BYTES"
    echo "boot_fits_cache=yes"
    echo "initramfs_extra_present=yes"
    echo "initramfs_present=yes"
    echo "dtb_present=yes"
    echo "e2fsck_read_only_rc=$E2FSCK_RC"
    echo "preparation_status=passed"
} | tee "$ARTIFACT_DIR/manifest.txt" | tee "$REPORT"

ln -sfn "$TIMESTAMP" "$CURRENT_LINK"
printf '%s  %s\n' "$FINAL_SHA" "$OUT_IMAGE" > "$OUT_IMAGE.sha256"

echo
echo "A33 cache pmOS boot image prepared."
echo "Image:   $OUT_IMAGE"
echo "Current: $CURRENT_LINK"
echo "SHA256:  $FINAL_SHA"
echo "No phone partition was written."

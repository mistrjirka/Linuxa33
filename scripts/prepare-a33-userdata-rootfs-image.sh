#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SOURCE_LINK="${SOURCE_LINK:-$PORT_ROOT/build/rootfs-images/current/samsung-a33x-root.img}"
SOURCE_MANIFEST="${SOURCE_MANIFEST:-$PORT_ROOT/build/rootfs-images/current/manifest.txt}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$PORT_ROOT/build/userdata-rootfs-images}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="$ARTIFACT_ROOT/$TIMESTAMP"
CURRENT_LINK="$ARTIFACT_ROOT/current"
OUT_IMAGE="$ARTIFACT_DIR/a33x-userdata-pmos-root.img"
REPORT="$PORT_ROOT/build/a33-userdata-rootfs-image.txt"

for command in readlink cp sha256sum stat blkid e2fsck debugfs grep awk sed find mkdir chmod; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

SOURCE_IMAGE="$(readlink -f "$SOURCE_LINK" 2>/dev/null || true)"
if [[ -z "$SOURCE_IMAGE" || ! -f "$SOURCE_IMAGE" ]]; then
    echo "REFUSING: finalized standalone root image is missing" >&2
    echo "source_link=$SOURCE_LINK" >&2
    exit 1
fi

MANIFEST="$(readlink -f "$SOURCE_MANIFEST" 2>/dev/null || true)"
if [[ -z "$MANIFEST" || ! -f "$MANIFEST" ]]; then
    echo "REFUSING: finalized root image manifest is missing" >&2
    echo "manifest_link=$SOURCE_MANIFEST" >&2
    exit 1
fi

manifest_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$MANIFEST"
}

if [[ "$(manifest_value preparation_status)" != passed ]]; then
    echo "REFUSING: source root image is not marked preparation_status=passed" >&2
    exit 1
fi

SOURCE_EXPECTED_SHA="$(manifest_value root_sha256)"
SOURCE_ACTUAL_SHA="$(sha256sum "$SOURCE_IMAGE" | awk '{print $1}')"
if [[ ! "$SOURCE_EXPECTED_SHA" =~ ^[0-9a-f]{64}$ || "$SOURCE_ACTUAL_SHA" != "$SOURCE_EXPECTED_SHA" ]]; then
    echo "REFUSING: source root image SHA256 does not match its manifest" >&2
    echo "expected=${SOURCE_EXPECTED_SHA:-missing}" >&2
    echo "actual=$SOURCE_ACTUAL_SHA" >&2
    exit 1
fi

SOURCE_TYPE="$(blkid -p -s TYPE -o value "$SOURCE_IMAGE" 2>/dev/null || true)"
SOURCE_LABEL="$(blkid -p -s LABEL -o value "$SOURCE_IMAGE" 2>/dev/null || true)"
SOURCE_UUID="$(blkid -p -s UUID -o value "$SOURCE_IMAGE" 2>/dev/null || true)"
SOURCE_SIZE="$(stat -Lc '%s' "$SOURCE_IMAGE")"

if [[ "$SOURCE_TYPE" != ext4 ]]; then
    echo "REFUSING: source root image is not ext4" >&2
    exit 1
fi
if [[ "$SOURCE_LABEL" != pmOS_root ]]; then
    echo "REFUSING: source root image label is not pmOS_root" >&2
    echo "actual_label=${SOURCE_LABEL:-none}" >&2
    exit 1
fi
if [[ -z "$SOURCE_UUID" ]]; then
    echo "REFUSING: source root image has no filesystem UUID" >&2
    exit 1
fi

mkdir -p "$ARTIFACT_DIR" "$PORT_ROOT/build"
chmod 700 "$ARTIFACT_DIR"
cp --reflink=auto --sparse=always --dereference "$SOURCE_IMAGE" "$OUT_IMAGE"

OLD_FSTAB="$ARTIFACT_DIR/fstab.original.txt"
NEW_FSTAB="$ARTIFACT_DIR/fstab.userdata.txt"
debugfs -R 'cat /etc/fstab' "$OUT_IMAGE" > "$OLD_FSTAB" 2> "$ARTIFACT_DIR/debugfs-read-fstab.stderr"

cat > "$NEW_FSTAB" <<EOF
# A33 internal-rootfs test: boot image remains in recovery; no separate /boot filesystem.
UUID=$SOURCE_UUID / ext4 defaults 0 1
EOF

MARKER="$ARTIFACT_DIR/a33x-rootfs-target.txt"
cat > "$MARKER" <<EOF
target=android-userdata
expected_block=/dev/block/by-name/userdata
expected_resolved=/dev/block/sda36
root_label=pmOS_root
root_uuid=$SOURCE_UUID
source_sha256=$SOURCE_ACTUAL_SHA
prepared=$(date -Ins)
EOF

# Modify only the copied deployment image.
debugfs -w -R 'rm /etc/fstab' "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-rm-fstab.txt" 2>&1
debugfs -w -R "write $NEW_FSTAB /etc/fstab" "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-write-fstab.txt" 2>&1
debugfs -w -R 'set_inode_field /etc/fstab mode 0100644' "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-mode-fstab.txt" 2>&1
debugfs -w -R 'set_inode_field /etc/fstab uid 0' "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-uid-fstab.txt" 2>&1
debugfs -w -R 'set_inode_field /etc/fstab gid 0' "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-gid-fstab.txt" 2>&1

if debugfs -R 'stat /etc/a33x-rootfs-target' "$OUT_IMAGE" 2>&1 | grep -q '^Inode:'; then
    debugfs -w -R 'rm /etc/a33x-rootfs-target' "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-rm-marker.txt" 2>&1
fi
debugfs -w -R "write $MARKER /etc/a33x-rootfs-target" "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-write-marker.txt" 2>&1
debugfs -w -R 'set_inode_field /etc/a33x-rootfs-target mode 0100644' "$OUT_IMAGE" > "$ARTIFACT_DIR/debugfs-mode-marker.txt" 2>&1

E2FSCK_RC=0
e2fsck -fn "$OUT_IMAGE" > "$ARTIFACT_DIR/e2fsck-read-only.txt" 2>&1 || E2FSCK_RC=$?
if [[ "$E2FSCK_RC" -ne 0 ]]; then
    echo "REFUSING: userdata deployment image failed read-only e2fsck (rc=$E2FSCK_RC)" >&2
    cat "$ARTIFACT_DIR/e2fsck-read-only.txt" >&2
    exit 1
fi

FINAL_FSTAB="$ARTIFACT_DIR/fstab.final.txt"
debugfs -R 'cat /etc/fstab' "$OUT_IMAGE" > "$FINAL_FSTAB" 2> "$ARTIFACT_DIR/debugfs-final-fstab.stderr"

non_comment_lines="$(grep -Ev '^[[:space:]]*(#|$)' "$FINAL_FSTAB" || true)"
if [[ "$(printf '%s\n' "$non_comment_lines" | grep -c . || true)" != 1 ]]; then
    echo "REFUSING: final fstab must contain exactly one active line" >&2
    cat "$FINAL_FSTAB" >&2
    exit 1
fi
if ! grep -Fqx "UUID=$SOURCE_UUID / ext4 defaults 0 1" "$FINAL_FSTAB"; then
    echo "REFUSING: final fstab root line is incorrect" >&2
    cat "$FINAL_FSTAB" >&2
    exit 1
fi
# Validate only active fstab entries. The explanatory comment intentionally
# mentions /boot and must not be interpreted as a mount declaration.
if grep -Eq '(^|[[:space:]])/boot([[:space:]]|$)' <<<"$non_comment_lines"; then
    echo "REFUSING: final fstab still contains an active /boot mount" >&2
    exit 1
fi

for required_path in \
    /sbin/init \
    /etc/os-release \
    /usr/sbin/sshd \
    /etc/runlevels/default/sshd \
    /etc/runlevels/default/networkmanager \
    /usr/libexec/a33x-muic-switch-dynamic \
    /etc/a33x-rootfs-target; do
    output="$(debugfs -R "stat $required_path" "$OUT_IMAGE" 2>&1 || true)"
    if ! grep -q '^Inode:' <<<"$output"; then
        echo "REFUSING: deployment image is missing $required_path" >&2
        echo "$output" >&2
        exit 1
    fi
done

FINAL_TYPE="$(blkid -p -s TYPE -o value "$OUT_IMAGE" 2>/dev/null || true)"
FINAL_LABEL="$(blkid -p -s LABEL -o value "$OUT_IMAGE" 2>/dev/null || true)"
FINAL_UUID="$(blkid -p -s UUID -o value "$OUT_IMAGE" 2>/dev/null || true)"
FINAL_SIZE="$(stat -Lc '%s' "$OUT_IMAGE")"
FINAL_SHA="$(sha256sum "$OUT_IMAGE" | awk '{print $1}')"

if [[ "$FINAL_TYPE" != ext4 || "$FINAL_LABEL" != pmOS_root || "$FINAL_UUID" != "$SOURCE_UUID" ]]; then
    echo "REFUSING: filesystem identity changed unexpectedly" >&2
    echo "type=$FINAL_TYPE label=$FINAL_LABEL uuid=$FINAL_UUID" >&2
    exit 1
fi
if [[ "$FINAL_SIZE" != "$SOURCE_SIZE" ]]; then
    echo "REFUSING: deployment image size changed unexpectedly" >&2
    echo "source=$SOURCE_SIZE final=$FINAL_SIZE" >&2
    exit 1
fi

{
    echo "created=$(date -Ins)"
    echo "operation=prepare-userdata-rootfs-deployment-image"
    echo "phone_required=no"
    echo "phone_partition_writes=no"
    echo "source_image=$SOURCE_IMAGE"
    echo "source_sha256=$SOURCE_ACTUAL_SHA"
    echo "source_size=$SOURCE_SIZE"
    echo "deployment_image=$OUT_IMAGE"
    echo "deployment_sha256=$FINAL_SHA"
    echo "deployment_size=$FINAL_SIZE"
    echo "root_type=$FINAL_TYPE"
    echo "root_label=$FINAL_LABEL"
    echo "root_uuid=$FINAL_UUID"
    echo "fstab_boot_mount_removed=yes"
    echo "fstab_root_only=yes"
    echo "u0g_payload_present=yes"
    echo "openssh_present=yes"
    echo "networkmanager_enabled=yes"
    echo "e2fsck_read_only_rc=$E2FSCK_RC"
    echo "preparation_status=passed"
} | tee "$ARTIFACT_DIR/manifest.txt" | tee "$REPORT"

ln -sfn "$TIMESTAMP" "$CURRENT_LINK"
printf '%s  %s\n' "$FINAL_SHA" "$OUT_IMAGE" > "$OUT_IMAGE.sha256"

echo
echo "A33 userdata rootfs deployment image prepared."
echo "Image:   $OUT_IMAGE"
echo "Current: $CURRENT_LINK"
echo "SHA256:  $FINAL_SHA"
echo "No phone partition was written."

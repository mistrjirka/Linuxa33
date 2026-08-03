#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

CONFIRMATION="${1:-}"
REQUIRED_CONFIRMATION="ERASE-ANDROID-USERDATA-INSTALL-PMOS"

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
IMAGE_LINK="${IMAGE_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img}"
IMAGE_MANIFEST_LINK="${IMAGE_MANIFEST_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt}"
BACKUP_ROOT="${BACKUP_ROOT:-$PORT_ROOT/build/private-backups}"
REPORT="$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA_RESOLVED="${EXPECTED_USERDATA_RESOLVED:-/dev/block/sda36}"
EXPECTED_USERDATA_BYTES="${EXPECTED_USERDATA_BYTES:-114240258048}"
TARGET="/dev/block/by-name/userdata"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: this command irreversibly erases Android userdata.

It destroys Android apps, accounts, settings, media, encryption state, and all
other files stored in userdata. It is not a complete Android removal: super and
Android boot remain untouched for the first controlled Linux test.

Run only after the private backup preflight passes, using the exact token:

  bash $0 $REQUIRED_CONFIRMATION
EOF
    exit 2
fi

for command in "$ADB" readlink sha256sum stat awk grep find sort date mkdir tee; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

if ! "$ADB" help 2>&1 | grep -q 'exec-in'; then
    echo "REFUSING: adb does not provide exec-in for raw input streaming" >&2
    exit 1
fi

IMAGE="$(readlink -f "$IMAGE_LINK" 2>/dev/null || true)"
IMAGE_MANIFEST="$(readlink -f "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$IMAGE" || ! -f "$IMAGE" || -z "$IMAGE_MANIFEST" || ! -f "$IMAGE_MANIFEST" ]]; then
    echo "REFUSING: prepared userdata image or manifest is missing" >&2
    exit 1
fi

manifest_value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

if [[ "$(manifest_value "$IMAGE_MANIFEST" preparation_status)" != passed ]]; then
    echo "REFUSING: image manifest is not preparation_status=passed" >&2
    exit 1
fi
IMAGE_EXPECTED_SHA="$(manifest_value "$IMAGE_MANIFEST" deployment_sha256)"
IMAGE_EXPECTED_SIZE="$(manifest_value "$IMAGE_MANIFEST" deployment_size)"
IMAGE_EXPECTED_UUID="$(manifest_value "$IMAGE_MANIFEST" root_uuid)"
IMAGE_ACTUAL_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_ACTUAL_SIZE="$(stat -Lc '%s' "$IMAGE")"
if [[ "$IMAGE_ACTUAL_SHA" != "$IMAGE_EXPECTED_SHA" || "$IMAGE_ACTUAL_SIZE" != "$IMAGE_EXPECTED_SIZE" ]]; then
    echo "REFUSING: deployment image differs from its manifest" >&2
    exit 1
fi
if (( IMAGE_ACTUAL_SIZE % 1048576 != 0 )); then
    echo "REFUSING: deployment image size is not an exact MiB multiple" >&2
    exit 1
fi
READBACK_MIB=$((IMAGE_ACTUAL_SIZE / 1048576))

PREFLIGHT_DIR="$(
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'a33-before-userdata-repurpose-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
PREFLIGHT_MANIFEST="$PREFLIGHT_DIR/manifest.txt"
if [[ -z "$PREFLIGHT_DIR" || ! -f "$PREFLIGHT_MANIFEST" ]]; then
    echo "REFUSING: private backup preflight is missing" >&2
    echo "Run scripts/backup-a33-before-userdata-repurpose.sh first." >&2
    exit 1
fi
if [[ "$(manifest_value "$PREFLIGHT_MANIFEST" backup_status)" != passed ]]; then
    echo "REFUSING: private backup did not pass" >&2
    exit 1
fi
if [[ "$(manifest_value "$PREFLIGHT_MANIFEST" deployment_sha256)" != "$IMAGE_ACTUAL_SHA" ]]; then
    echo "REFUSING: private backup was made for a different deployment image" >&2
    exit 1
fi
if [[ "$(manifest_value "$PREFLIGHT_MANIFEST" userdata_resolved)" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" userdata_bytes)" != "$EXPECTED_USERDATA_BYTES" ]]; then
    echo "REFUSING: private backup was made for a different userdata mapping" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"

echo "=== Wait for exact known-good TWRP ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

remote_value() {
    "$ADB" shell "$1" 2>/dev/null | tr -d '\r' | head -n 1
}

RECOVERY_SHA="$(remote_value 'sha256sum /dev/block/by-name/recovery | awk "NR==1 {print \$1}"')"
USERDATA_RESOLVED="$(remote_value 'readlink -f /dev/block/by-name/userdata')"
USERDATA_BYTES="$(remote_value 'blockdev --getsize64 /dev/block/by-name/userdata')"
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

if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running exact known-good TWRP" >&2
    exit 1
fi
if [[ "$USERDATA_RESOLVED" != "$EXPECTED_USERDATA_RESOLVED" || "$USERDATA_BYTES" != "$EXPECTED_USERDATA_BYTES" ]]; then
    echo "REFUSING: userdata identity changed" >&2
    echo "resolved=$USERDATA_RESOLVED bytes=$USERDATA_BYTES" >&2
    exit 1
fi
if [[ -n "$DATA_MOUNT_SOURCE" ]]; then
    echo "REFUSING: /data is mounted: $DATA_MOUNT_SOURCE" >&2
    exit 1
fi
if [[ -n "$USERDATA_DM_USERS" ]]; then
    echo "REFUSING: userdata is in use by device-mapper" >&2
    echo "$USERDATA_DM_USERS" >&2
    exit 1
fi
if (( IMAGE_ACTUAL_SIZE >= USERDATA_BYTES )); then
    echo "REFUSING: image does not fit userdata" >&2
    exit 1
fi

cat <<EOF

DESTRUCTIVE WRITE AUTHORIZED
  target:          $TARGET
  resolved:        $USERDATA_RESOLVED
  target bytes:    $USERDATA_BYTES
  image:           $IMAGE
  image bytes:     $IMAGE_ACTUAL_SIZE
  image SHA256:    $IMAGE_ACTUAL_SHA
  private backup:  $PREFLIGHT_DIR

Only userdata will be overwritten. super, boot, and recovery are not touched by
this script.
EOF

STARTED="$(date -Ins)"
echo "=== Stream validated root image to Android userdata ==="
if command -v pv >/dev/null 2>&1; then
    pv -s "$IMAGE_ACTUAL_SIZE" "$IMAGE" | \
        "$ADB" exec-in sh -c "dd of='$TARGET' bs=1048576 2>/tmp/a33x-userdata-dd.log; rc=\$?; sync; cat /tmp/a33x-userdata-dd.log >&2; exit \$rc"
else
    "$ADB" exec-in sh -c "dd of='$TARGET' bs=1048576 2>/tmp/a33x-userdata-dd.log; rc=\$?; sync; cat /tmp/a33x-userdata-dd.log >&2; exit \$rc" < "$IMAGE"
fi

WRITTEN_PREFIX_SHA="$(
    "$ADB" exec-out sh -c "dd if='$TARGET' bs=1048576 count='$READBACK_MIB' 2>/dev/null" \
    | sha256sum \
    | awk '{print $1}'
)"
if [[ "$WRITTEN_PREFIX_SHA" != "$IMAGE_ACTUAL_SHA" ]]; then
    echo "REFUSING: userdata readback SHA256 mismatch" >&2
    echo "expected=$IMAGE_ACTUAL_SHA actual=$WRITTEN_PREFIX_SHA" >&2
    exit 1
fi

REMOTE_IDENTITY="$(
    "$ADB" shell sh -s -- "$TARGET" 2>/dev/null <<'SH' | tr -d '\r'
set -u
target="$1"
echo "type=$(blkid -s TYPE -o value "$target" 2>/dev/null || true)"
echo "label=$(blkid -s LABEL -o value "$target" 2>/dev/null || true)"
echo "uuid=$(blkid -s UUID -o value "$target" 2>/dev/null || true)"
SH
)"
REMOTE_TYPE="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="type" {print $2; exit}')"
REMOTE_LABEL="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="label" {print $2; exit}')"
REMOTE_UUID="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="uuid" {print $2; exit}')"
if [[ "$REMOTE_TYPE" != ext4 || "$REMOTE_LABEL" != pmOS_root || "$REMOTE_UUID" != "$IMAGE_EXPECTED_UUID" ]]; then
    echo "REFUSING: written filesystem identity is wrong" >&2
    echo "$REMOTE_IDENTITY" >&2
    exit 1
fi

VERIFY_OUTPUT="$(
    "$ADB" shell sh -s -- "$TARGET" 2>/dev/null <<'SH' | tr -d '\r'
set -u
target="$1"
mountpoint=/tmp/a33x-userdata-root-verify
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload "$target" "$mountpoint"
cleanup() { umount "$mountpoint" 2>/dev/null || true; }
trap cleanup EXIT
for path in \
    /sbin/init \
    /etc/os-release \
    /usr/sbin/sshd \
    /etc/runlevels/default/sshd \
    /etc/runlevels/default/networkmanager \
    /usr/libexec/a33x-muic-switch-dynamic \
    /etc/a33x-rootfs-target; do
    if [ ! -e "$mountpoint$path" ]; then
        echo "missing=$path"
        exit 1
    fi
done
echo "fstab_begin"
cat "$mountpoint/etc/fstab"
echo "fstab_end"
echo "marker_begin"
cat "$mountpoint/etc/a33x-rootfs-target"
echo "marker_end"
echo "verify_status=passed"
SH
)"
if ! grep -Fq 'verify_status=passed' <<<"$VERIFY_OUTPUT"; then
    echo "REFUSING: read-only verification mount failed" >&2
    echo "$VERIFY_OUTPUT" >&2
    exit 1
fi
FSTAB_ACTIVE="$(
    printf '%s\n' "$VERIFY_OUTPUT" \
    | awk '/^fstab_begin$/ {inside=1; next} /^fstab_end$/ {inside=0} inside' \
    | grep -Ev '^[[:space:]]*(#|$)' || true
)"
if [[ "$FSTAB_ACTIVE" != "UUID=$IMAGE_EXPECTED_UUID / ext4 defaults 0 1" ]]; then
    echo "REFUSING: deployed fstab is incorrect" >&2
    echo "$FSTAB_ACTIVE" >&2
    exit 1
fi

FINISHED="$(date -Ins)"
PREFLIGHT_MANIFEST_SHA="$(sha256sum "$PREFLIGHT_MANIFEST" | awk '{print $1}')"
{
    echo "started=$STARTED"
    echo "finished=$FINISHED"
    echo "operation=destructive-userdata-rootfs-deployment"
    echo "android_userdata_erased=yes"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=no"
    echo "target=$TARGET"
    echo "target_resolved=$USERDATA_RESOLVED"
    echo "target_bytes=$USERDATA_BYTES"
    echo "deployment_image=$IMAGE"
    echo "deployment_size=$IMAGE_ACTUAL_SIZE"
    echo "deployment_sha256=$IMAGE_ACTUAL_SHA"
    echo "written_prefix_sha256=$WRITTEN_PREFIX_SHA"
    echo "filesystem_type=$REMOTE_TYPE"
    echo "filesystem_label=$REMOTE_LABEL"
    echo "filesystem_uuid=$REMOTE_UUID"
    echo "read_only_mount_verification=passed"
    echo "private_backup_dir=$PREFLIGHT_DIR"
    echo "private_backup_manifest_sha256=$PREFLIGHT_MANIFEST_SHA"
    echo "next_boot_image=exact-u0g-recovery"
    echo "next_boot_expected_recovery_sha256=e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"
    echo "deployment_status=passed"
} | tee "$REPORT"

echo
echo "A33 postmarketOS rootfs was written to userdata and verified."
echo "Android userdata has been erased."
echo "Report: $REPORT"
echo "Do not boot Android. Flash and boot the exact U0g recovery candidate next."

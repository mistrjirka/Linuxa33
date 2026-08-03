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
HANDOFF_REPORT="${HANDOFF_REPORT:-$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt}"
BACKUP_ROOT="${BACKUP_ROOT:-$PORT_ROOT/build/private-backups}"
REPORT="$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt"
BACKUP_CHECK_LOG="$PORT_ROOT/build/a33-userdata-private-backup-check.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_U0G_RECOVERY_SHA256="e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"
EXPECTED_U0G_RAMDISK_SHA256="13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d"
EXPECTED_USERDATA_RESOLVED="${EXPECTED_USERDATA_RESOLVED:-/dev/block/sda36}"
EXPECTED_USERDATA_BYTES="${EXPECTED_USERDATA_BYTES:-114240258048}"
TARGET="/dev/block/by-name/userdata"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: this command irreversibly erases Android userdata.

It destroys Android apps, accounts, settings, media, encryption state, and all
other files stored in userdata. It is not a complete Android removal: super and
Android boot remain untouched for the first controlled Linux test.

Run only after every non-destructive validation passes, using the exact token:

  bash $0 $REQUIRED_CONFIRMATION
EOF
    exit 2
fi

for command in "$ADB" readlink sha256sum stat awk grep find sort date mkdir tee realpath; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

if ! "$ADB" help 2>&1 | grep -q 'exec-in'; then
    echo "REFUSING: adb does not provide exec-in for raw input streaming" >&2
    exit 1
fi

manifest_value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

for required in "$HANDOFF_REPORT" "$IMAGE_LINK" "$IMAGE_MANIFEST_LINK"; do
    [[ -e "$required" ]] || {
        echo "REFUSING: required deployment input is missing: $required" >&2
        exit 1
    }
done

if [[ "$(manifest_value "$HANDOFF_REPORT" verification_status)" != passed || \
      "$(manifest_value "$HANDOFF_REPORT" cache_partition_required)" != no || \
      "$(manifest_value "$HANDOFF_REPORT" pmos_boot_required_before_second_stage)" != no || \
      "$(manifest_value "$HANDOFF_REPORT" init_2nd_embedded)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" init_2nd_executable)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" init_2nd_invocation_before_extra)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" pmos_root_discovery)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" root_wait_present)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" root_mount_present)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" switch_root_present)" != yes || \
      "$(manifest_value "$HANDOFF_REPORT" recovery_sha256)" != "$EXPECTED_U0G_RECOVERY_SHA256" || \
      "$(manifest_value "$HANDOFF_REPORT" ramdisk_sha256)" != "$EXPECTED_U0G_RAMDISK_SHA256" ]]; then
    echo "REFUSING: exact U0g unified-root handoff report did not pass" >&2
    cat "$HANDOFF_REPORT" >&2
    exit 1
fi
HANDOFF_REPORT_SHA="$(sha256sum "$HANDOFF_REPORT" | awk '{print $1}')"

IMAGE="$(readlink -f "$IMAGE_LINK" 2>/dev/null || true)"
IMAGE_MANIFEST="$(readlink -f "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$IMAGE" || ! -f "$IMAGE" || -z "$IMAGE_MANIFEST" || ! -f "$IMAGE_MANIFEST" ]]; then
    echo "REFUSING: prepared userdata image or manifest is missing" >&2
    exit 1
fi

if [[ "$(manifest_value "$IMAGE_MANIFEST" preparation_status)" != passed || \
      "$(manifest_value "$IMAGE_MANIFEST" root_type)" != ext4 || \
      "$(manifest_value "$IMAGE_MANIFEST" root_label)" != pmOS_root || \
      "$(manifest_value "$IMAGE_MANIFEST" fstab_root_only)" != yes || \
      "$(manifest_value "$IMAGE_MANIFEST" fstab_boot_mount_removed)" != yes || \
      "$(manifest_value "$IMAGE_MANIFEST" openssh_present)" != yes || \
      "$(manifest_value "$IMAGE_MANIFEST" networkmanager_enabled)" != yes || \
      "$(manifest_value "$IMAGE_MANIFEST" u0g_payload_present)" != yes ]]; then
    echo "REFUSING: userdata image manifest did not pass the required contract" >&2
    cat "$IMAGE_MANIFEST" >&2
    exit 1
fi

IMAGE_EXPECTED_SHA="$(manifest_value "$IMAGE_MANIFEST" deployment_sha256)"
IMAGE_EXPECTED_SIZE="$(manifest_value "$IMAGE_MANIFEST" deployment_size)"
IMAGE_EXPECTED_UUID="$(manifest_value "$IMAGE_MANIFEST" root_uuid)"
IMAGE_ACTUAL_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_ACTUAL_SIZE="$(stat -Lc '%s' "$IMAGE")"
if [[ ! "$IMAGE_EXPECTED_SHA" =~ ^[0-9a-f]{64}$ || \
      ! "$IMAGE_EXPECTED_SIZE" =~ ^[0-9]+$ || \
      -z "$IMAGE_EXPECTED_UUID" || \
      "$IMAGE_ACTUAL_SHA" != "$IMAGE_EXPECTED_SHA" || \
      "$IMAGE_ACTUAL_SIZE" != "$IMAGE_EXPECTED_SIZE" ]]; then
    echo "REFUSING: deployment image differs from its manifest" >&2
    echo "expected_sha=$IMAGE_EXPECTED_SHA actual_sha=$IMAGE_ACTUAL_SHA" >&2
    echo "expected_size=$IMAGE_EXPECTED_SIZE actual_size=$IMAGE_ACTUAL_SIZE" >&2
    exit 1
fi
if (( IMAGE_ACTUAL_SIZE % 1048576 != 0 )); then
    echo "REFUSING: deployment image size is not an exact MiB multiple" >&2
    exit 1
fi
READBACK_MIB=$((IMAGE_ACTUAL_SIZE / 1048576))
IMAGE_MANIFEST_SHA="$(sha256sum "$IMAGE_MANIFEST" | awk '{print $1}')"

PREFLIGHT_DIR="$(
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'a33-before-userdata-repurpose-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
PREFLIGHT_DIR="$(realpath -e "$PREFLIGHT_DIR" 2>/dev/null || true)"
BACKUP_ROOT_REAL="$(realpath -e "$BACKUP_ROOT" 2>/dev/null || true)"
if [[ -z "$PREFLIGHT_DIR" || -z "$BACKUP_ROOT_REAL" || \
      "$PREFLIGHT_DIR" != "$BACKUP_ROOT_REAL"/a33-before-userdata-repurpose-* ]]; then
    echo "REFUSING: private backup preflight directory is missing or outside backup root" >&2
    exit 1
fi

PREFLIGHT_MANIFEST="$PREFLIGHT_DIR/manifest.txt"
PREFLIGHT_SUMS="$PREFLIGHT_DIR/SHA256SUMS"
PREFLIGHT_PUBLIC_COPY="$PREFLIGHT_DIR/public-summary-copy.txt"
for required in \
    "$PREFLIGHT_MANIFEST" \
    "$PREFLIGHT_SUMS" \
    "$PREFLIGHT_PUBLIC_COPY" \
    "$PREFLIGHT_DIR/ufs-gpt-primary-and-prefix.bin" \
    "$PREFLIGHT_DIR/ufs-gpt-backup-and-suffix.bin" \
    "$PREFLIGHT_DIR/userdata-first-16MiB.bin" \
    "$PREFLIGHT_DIR/userdata-last-16MiB.bin" \
    "$PREFLIGHT_DIR/partition-boot.img" \
    "$PREFLIGHT_DIR/partition-recovery.img"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: required private rescue artifact is missing: $required" >&2
        exit 1
    }
done

if [[ "$(manifest_value "$PREFLIGHT_MANIFEST" backup_status)" != passed || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" deployment_sha256)" != "$IMAGE_ACTUAL_SHA" || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" deployment_size)" != "$IMAGE_ACTUAL_SIZE" || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" userdata_resolved)" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" userdata_bytes)" != "$EXPECTED_USERDATA_BYTES" || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" userdata_mounted)" != no || \
      "$(manifest_value "$PREFLIGHT_MANIFEST" userdata_device_mapper_users)" != none ]]; then
    echo "REFUSING: private backup manifest does not match this deployment" >&2
    cat "$PREFLIGHT_MANIFEST" >&2
    exit 1
fi
if [[ "$(manifest_value "$PREFLIGHT_PUBLIC_COPY" preflight_status)" != passed || \
      "$(manifest_value "$PREFLIGHT_PUBLIC_COPY" deployment_sha256)" != "$IMAGE_ACTUAL_SHA" || \
      "$(manifest_value "$PREFLIGHT_PUBLIC_COPY" private_backup_status)" != passed ]]; then
    echo "REFUSING: copied sanitized preflight summary did not pass" >&2
    cat "$PREFLIGHT_PUBLIC_COPY" >&2
    exit 1
fi

if [[ "$(stat -Lc '%s' "$PREFLIGHT_DIR/ufs-gpt-primary-and-prefix.bin")" != 4194304 || \
      "$(stat -Lc '%s' "$PREFLIGHT_DIR/ufs-gpt-backup-and-suffix.bin")" != 4194304 || \
      "$(stat -Lc '%s' "$PREFLIGHT_DIR/userdata-first-16MiB.bin")" != 16777216 || \
      "$(stat -Lc '%s' "$PREFLIGHT_DIR/userdata-last-16MiB.bin")" != 16777216 ]]; then
    echo "REFUSING: private GPT/userdata rescue ranges have unexpected sizes" >&2
    exit 1
fi

(
    cd /
    sha256sum -c "$PREFLIGHT_SUMS"
) > "$BACKUP_CHECK_LOG" 2>&1 || {
    echo "REFUSING: private backup checksum verification failed" >&2
    cat "$BACKUP_CHECK_LOG" >&2
    exit 1
}

BACKED_UP_RECOVERY_SHA="$(sha256sum "$PREFLIGHT_DIR/partition-recovery.img" | awk '{print $1}')"
if [[ "$BACKED_UP_RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: private recovery backup is not exact known-good TWRP" >&2
    echo "actual=$BACKED_UP_RECOVERY_SHA" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"

echo "=== Wait for exact known-good TWRP ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

LIVE_STATE="$(
    "$ADB" shell sh -s -- "$TARGET" "$EXPECTED_USERDATA_RESOLVED" 2>/dev/null <<'SH' | tr -d '\r'
set -u
target="$1"
expected_resolved="$2"
resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"
echo "userdata_resolved=$resolved"
echo "userdata_bytes=$(blockdev --getsize64 "$target" 2>/dev/null || true)"
echo "userdata_readonly=$(blockdev --getro "$target" 2>/dev/null || true)"

echo "mount_users_begin"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mountpoint; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$source $mountpoint"
    fi
done
echo "mount_users_end"

echo "swap_users_begin"
if [ -r /proc/swaps ]; then
    tail -n +2 /proc/swaps 2>/dev/null | while read -r source rest; do
        source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
        if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
            echo "$source"
        fi
    done
echo "swap_users_end"
fi

echo "dm_users_begin"
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    if find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -qx "${resolved##*/}"; then
        echo "${dm##*/}:$(cat "$dm/dm/name" 2>/dev/null || true)"
    fi
done
echo "dm_users_end"
SH
)"

state_value() {
    local key="$1"
    printf '%s\n' "$LIVE_STATE" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}
state_section() {
    local section="$1"
    printf '%s\n' "$LIVE_STATE" | awk -v begin="${section}_begin" -v end="${section}_end" '
        $0==begin {inside=1; next}
        $0==end {inside=0}
        inside && NF {print}
    '
}

RECOVERY_SHA="$(state_value recovery_sha)"
USERDATA_RESOLVED="$(state_value userdata_resolved)"
USERDATA_BYTES="$(state_value userdata_bytes)"
USERDATA_READONLY="$(state_value userdata_readonly)"
MOUNT_USERS="$(state_section mount_users)"
SWAP_USERS="$(state_section swap_users)"
DM_USERS="$(state_section dm_users)"

if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running exact known-good TWRP" >&2
    exit 1
fi
if [[ "$USERDATA_RESOLVED" != "$EXPECTED_USERDATA_RESOLVED" || "$USERDATA_BYTES" != "$EXPECTED_USERDATA_BYTES" ]]; then
    echo "REFUSING: userdata identity changed" >&2
    echo "resolved=$USERDATA_RESOLVED bytes=$USERDATA_BYTES" >&2
    exit 1
fi
if [[ "$USERDATA_READONLY" != 0 ]]; then
    echo "REFUSING: userdata block device is read-only or status is unknown" >&2
    echo "readonly=${USERDATA_READONLY:-missing}" >&2
    exit 1
fi
if [[ -n "$MOUNT_USERS" || -n "$SWAP_USERS" || -n "$DM_USERS" ]]; then
    echo "REFUSING: userdata is still in use" >&2
    printf 'mount_users=%s\nswap_users=%s\ndm_users=%s\n' "$MOUNT_USERS" "$SWAP_USERS" "$DM_USERS" >&2
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
  handoff report:  $HANDOFF_REPORT
  private backup:  $PREFLIGHT_DIR

Only userdata will be overwritten. cache, super, boot, and recovery are not
touched by this script.
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
set -eu
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
    "$ADB" shell sh -s -- "$TARGET" "$IMAGE_EXPECTED_UUID" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
target="$1"
expected_uuid="$2"
mountpoint=/tmp/a33x-userdata-root-verify
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
cleanup() { umount "$mountpoint" 2>/dev/null || true; }
trap cleanup EXIT
mount -t ext4 -o ro,noload "$target" "$mountpoint"
for path in \
    /sbin/init \
    /etc/os-release \
    /usr/sbin/sshd \
    /etc/runlevels/default/sshd \
    /etc/runlevels/default/networkmanager \
    /usr/libexec/a33x-muic-switch-dynamic \
    /etc/a33x-rootfs-target; do
    [ -e "$mountpoint$path" ] || {
        echo "missing=$path"
        exit 1
    }
done
echo "fstab_begin"
cat "$mountpoint/etc/fstab"
echo "fstab_end"
echo "marker_begin"
cat "$mountpoint/etc/a33x-rootfs-target"
echo "marker_end"
grep -Fqx "root_uuid=$expected_uuid" "$mountpoint/etc/a33x-rootfs-target"
grep -Fqx 'target=android-userdata' "$mountpoint/etc/a33x-rootfs-target"
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

POST_VERIFY_MOUNTS="$(
    "$ADB" shell sh -s -- "$TARGET" "$USERDATA_RESOLVED" 2>/dev/null <<'SH' | tr -d '\r'
target="$1"
resolved="$2"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mountpoint; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$source $mountpoint"
    fi
done
SH
)"
if [[ -n "$POST_VERIFY_MOUNTS" ]]; then
    echo "REFUSING: verification mount cleanup did not complete" >&2
    echo "$POST_VERIFY_MOUNTS" >&2
    exit 1
fi

FINISHED="$(date -Ins)"
PREFLIGHT_MANIFEST_SHA="$(sha256sum "$PREFLIGHT_MANIFEST" | awk '{print $1}')"
PREFLIGHT_SUMS_SHA="$(sha256sum "$PREFLIGHT_SUMS" | awk '{print $1}')"
{
    echo "started=$STARTED"
    echo "finished=$FINISHED"
    echo "operation=destructive-userdata-rootfs-deployment"
    echo "android_userdata_erased=yes"
    echo "cache_written=no"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=no"
    echo "target=$TARGET"
    echo "target_resolved=$USERDATA_RESOLVED"
    echo "target_bytes=$USERDATA_BYTES"
    echo "deployment_image=$IMAGE"
    echo "deployment_manifest_sha256=$IMAGE_MANIFEST_SHA"
    echo "deployment_size=$IMAGE_ACTUAL_SIZE"
    echo "deployment_sha256=$IMAGE_ACTUAL_SHA"
    echo "written_prefix_sha256=$WRITTEN_PREFIX_SHA"
    echo "filesystem_type=$REMOTE_TYPE"
    echo "filesystem_label=$REMOTE_LABEL"
    echo "filesystem_uuid=$REMOTE_UUID"
    echo "read_only_mount_verification=passed"
    echo "verification_mount_cleanup=passed"
    echo "handoff_report=$HANDOFF_REPORT"
    echo "handoff_report_sha256=$HANDOFF_REPORT_SHA"
    echo "private_backup_dir=$PREFLIGHT_DIR"
    echo "private_backup_manifest_sha256=$PREFLIGHT_MANIFEST_SHA"
    echo "private_backup_sha256sums_sha256=$PREFLIGHT_SUMS_SHA"
    echo "private_backup_checksum_verification=passed"
    echo "next_boot_image=exact-u0g-recovery"
    echo "next_boot_expected_recovery_sha256=$EXPECTED_U0G_RECOVERY_SHA256"
    echo "deployment_status=passed"
} | tee "$REPORT"

echo
echo "A33 postmarketOS rootfs was written to userdata and verified."
echo "Android userdata has been erased."
echo "Report: $REPORT"
echo "Do not boot Android. Flash and boot the exact U0g recovery candidate next."

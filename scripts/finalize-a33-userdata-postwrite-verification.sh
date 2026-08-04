#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"

IMAGE_LINK="${IMAGE_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img}"
IMAGE_MANIFEST_LINK="${IMAGE_MANIFEST_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt}"
STAGE_REPORT="${STAGE_REPORT:-$PORT_ROOT/build/a33-userdata-rootfs-stage.txt}"
TRANSPORT_AUDIT_REPORT="${TRANSPORT_AUDIT_REPORT:-$PORT_ROOT/build/a33-first-rootfs-transport-final-audit.txt}"
HANDOFF_REPORT="${HANDOFF_REPORT:-$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt}"
REPORT="$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_U0G_RECOVERY_SHA256="e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"
EXPECTED_USERDATA_RESOLVED="/dev/block/sda36"
EXPECTED_USERDATA_BYTES="114240258048"
TARGET="/dev/block/by-name/userdata"
REMOTE_IMAGE="/tmp/a33x-userdata-pmos-root.img"

for command in "$ADB" readlink sha256sum stat awk grep date mkdir python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

for required in \
    "$IMAGE_LINK" "$IMAGE_MANIFEST_LINK" "$STAGE_REPORT" \
    "$TRANSPORT_AUDIT_REPORT" "$HANDOFF_REPORT"; do
    [[ -e "$required" ]] || {
        echo "REFUSING: required postwrite verification input is missing: $required" >&2
        exit 1
    }
done

if [[ "$(value "$TRANSPORT_AUDIT_REPORT" transport_final_audit_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" final_chain_audit_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" staging_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" persistent_phone_partition_writes)" != no ]]; then
    echo "REFUSING: pre-write transport audit is not valid" >&2
    exit 1
fi
if [[ "$(value "$HANDOFF_REPORT" verification_status)" != passed || \
      "$(value "$HANDOFF_REPORT" recovery_sha256)" != "$EXPECTED_U0G_RECOVERY_SHA256" ]]; then
    echo "REFUSING: U0g handoff report is not valid" >&2
    exit 1
fi

IMAGE="$(readlink -f "$IMAGE_LINK" 2>/dev/null || true)"
MANIFEST="$(readlink -f "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
[[ -f "$IMAGE" && -f "$MANIFEST" ]] || {
    echo "REFUSING: deployment image or manifest is missing" >&2
    exit 1
}
IMAGE_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_SIZE="$(stat -Lc '%s' "$IMAGE")"
IMAGE_UUID="$(value "$MANIFEST" root_uuid)"
if [[ "$(value "$MANIFEST" preparation_status)" != passed || \
      "$(value "$MANIFEST" deployment_sha256)" != "$IMAGE_SHA" || \
      "$(value "$MANIFEST" deployment_size)" != "$IMAGE_SIZE" || \
      "$(value "$MANIFEST" root_type)" != ext4 || \
      "$(value "$MANIFEST" root_label)" != pmOS_root || \
      -z "$IMAGE_UUID" || $((IMAGE_SIZE % 1048576)) -ne 0 ]]; then
    echo "REFUSING: local deployment image identity is invalid" >&2
    exit 1
fi
READBACK_MIB=$((IMAGE_SIZE / 1048576))

STARTED="$(date -Ins)"
echo "=== Wait for exact known-good TWRP ==="
a33_init_recovery_adb 30

LIVE_STATE="$(
    "$ADB" shell sh -s -- "$TARGET" 2>/dev/null <<'SH' | tr -d '\r'
set -u
target="$1"
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
fi
echo "swap_users_end"
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
    local name="$1"
    printf '%s\n' "$LIVE_STATE" | awk -v begin="${name}_begin" -v end="${name}_end" '
        $0==begin {inside=1; next}
        $0==end {inside=0}
        inside && NF {print}
    '
}
if [[ "$(state_value recovery_sha)" != "$KNOWN_TWRP_SHA256" || \
      "$(state_value userdata_resolved)" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$(state_value userdata_bytes)" != "$EXPECTED_USERDATA_BYTES" || \
      "$(state_value userdata_readonly)" != 0 || \
      -n "$(state_section mount_users)" || \
      -n "$(state_section swap_users)" || \
      -n "$(state_section dm_users)" ]]; then
    echo "REFUSING: TWRP or userdata live state is unsafe" >&2
    printf '%s\n' "$LIVE_STATE" >&2
    exit 1
fi

echo "=== Verify complete written prefix without rewriting userdata ==="
WRITTEN_PREFIX_META="$(
    "$ADB" exec-out sh -c "dd if='$TARGET' bs=1048576 count='$READBACK_MIB' 2>/dev/null" |
        python3 -c 'import hashlib,sys
h=hashlib.sha256(); n=0
while True:
    block=sys.stdin.buffer.read(1024*1024)
    if not block: break
    n += len(block); h.update(block)
print(n, h.hexdigest())'
)"
WRITTEN_PREFIX_SIZE="$(awk '{print $1}' <<<"$WRITTEN_PREFIX_META")"
WRITTEN_PREFIX_SHA="$(awk '{print $2}' <<<"$WRITTEN_PREFIX_META")"
if [[ "$WRITTEN_PREFIX_SIZE" != "$IMAGE_SIZE" || "$WRITTEN_PREFIX_SHA" != "$IMAGE_SHA" ]]; then
    echo "REFUSING: userdata prefix differs from the validated image" >&2
    echo "expected_size=$IMAGE_SIZE actual_size=$WRITTEN_PREFIX_SIZE" >&2
    echo "expected_sha=$IMAGE_SHA actual_sha=$WRITTEN_PREFIX_SHA" >&2
    exit 1
fi

IDENTITY="$(a33_ext4_identity "$TARGET")"
FS_TYPE="$(awk -F= '$1=="type" {print $2; exit}' <<<"$IDENTITY")"
FS_LABEL="$(awk -F= '$1=="label" {print $2; exit}' <<<"$IDENTITY")"
FS_UUID="$(awk -F= '$1=="uuid" {print $2; exit}' <<<"$IDENTITY")"
if [[ "$FS_TYPE" != ext4 || "$FS_LABEL" != pmOS_root || "$FS_UUID" != "$IMAGE_UUID" ]]; then
    echo "REFUSING: userdata filesystem identity is wrong" >&2
    printf '%s\n' "$IDENTITY" >&2
    exit 1
fi

echo "=== Mount userdata read-only and validate rootfs content ==="
VERIFY_OUTPUT="$(
    "$ADB" shell sh -s -- "$TARGET" "$IMAGE_UUID" 2>&1 <<'SH' | tr -d '\r'
set -u
target="$1"
expected_uuid="$2"
mountpoint=/tmp/a33x-userdata-root-verify
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
cleanup() { umount "$mountpoint" 2>/dev/null || true; }
trap cleanup EXIT
if ! mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"; then
    echo "mount_status=failed"
    dmesg 2>/dev/null | tail -n 80 || true
    exit 20
fi
echo "mount_status=passed"
for path in \
    /sbin/init \
    /etc/os-release \
    /usr/sbin/sshd \
    /etc/init.d/sshd \
    /etc/init.d/networkmanager \
    /usr/libexec/a33x-muic-switch-dynamic \
    /etc/a33x-rootfs-target \
    /etc/fstab; do
    [ -e "$mountpoint$path" ] || {
        echo "missing_regular=$path"
        exit 21
    }
done
verify_absolute_root_link() {
    path="$1"
    expected="$2"
    [ -L "$mountpoint$path" ] || {
        echo "missing_symlink=$path"
        exit 22
    }
    actual="$(readlink "$mountpoint$path")"
    [ "$actual" = "$expected" ] || {
        echo "wrong_symlink=$path actual=$actual expected=$expected"
        exit 23
    }
    [ -e "$mountpoint$expected" ] || {
        echo "missing_symlink_target=$path target=$expected"
        exit 24
    }
    echo "symlink_ok=$path->$expected"
}
verify_absolute_root_link /etc/runlevels/default/sshd /etc/init.d/sshd
verify_absolute_root_link /etc/runlevels/default/networkmanager /etc/init.d/networkmanager

echo "fstab_begin"
cat "$mountpoint/etc/fstab"
echo "fstab_end"
grep -Fqx "root_uuid=$expected_uuid" "$mountpoint/etc/a33x-rootfs-target" || exit 25
grep -Fqx 'target=android-userdata' "$mountpoint/etc/a33x-rootfs-target" || exit 26
echo "verify_status=passed"
SH
)" || {
    rc=$?
    echo "REFUSING: read-only postwrite verification failed (rc=$rc)" >&2
    printf '%s\n' "$VERIFY_OUTPUT" >&2
    exit "$rc"
}
if ! grep -Fqx 'verify_status=passed' <<<"$VERIFY_OUTPUT"; then
    echo "REFUSING: read-only postwrite verification did not complete" >&2
    printf '%s\n' "$VERIFY_OUTPUT" >&2
    exit 1
fi
FSTAB_ACTIVE="$(
    printf '%s\n' "$VERIFY_OUTPUT" |
        awk '/^fstab_begin$/ {inside=1; next} /^fstab_end$/ {inside=0} inside' |
        grep -Ev '^[[:space:]]*(#|$)' || true
)"
if [[ "$FSTAB_ACTIVE" != "UUID=$IMAGE_UUID / ext4 defaults 0 1" ]]; then
    echo "REFUSING: deployed fstab is incorrect" >&2
    printf '%s\n' "$FSTAB_ACTIVE" >&2
    exit 1
fi

POST_MOUNTS="$(
    "$ADB" shell sh -s -- "$TARGET" "$EXPECTED_USERDATA_RESOLVED" 2>/dev/null <<'SH' | tr -d '\r'
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
if [[ -n "$POST_MOUNTS" ]]; then
    echo "REFUSING: read-only verification mount was not cleaned up" >&2
    printf '%s\n' "$POST_MOUNTS" >&2
    exit 1
fi

FINISHED="$(date -Ins)"
{
    echo "started=$STARTED"
    echo "finished=$FINISHED"
    echo "operation=finalize-destructive-userdata-rootfs-deployment-after-verifier-false-negative"
    echo "postwrite_verifier=$SELF"
    echo "postwrite_verifier_sha256=$(sha256sum "$SELF" | awk '{print $1}')"
    echo "deployment_transport=verified-adb-push-staged-file"
    echo "adb_exec_in_used=no"
    echo "android_userdata_erased=yes"
    echo "cache_written=no"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=no"
    echo "target=$TARGET"
    echo "target_resolved=$EXPECTED_USERDATA_RESOLVED"
    echo "target_bytes=$EXPECTED_USERDATA_BYTES"
    echo "deployment_image=$IMAGE"
    echo "deployment_size=$IMAGE_SIZE"
    echo "deployment_sha256=$IMAGE_SHA"
    echo "written_prefix_size=$WRITTEN_PREFIX_SIZE"
    echo "written_prefix_sha256=$WRITTEN_PREFIX_SHA"
    echo "filesystem_type=$FS_TYPE"
    echo "filesystem_label=$FS_LABEL"
    echo "filesystem_uuid=$FS_UUID"
    echo "read_only_mount_verification=passed"
    echo "openrc_absolute_symlink_verification=passed"
    echo "verification_mount_cleanup=passed"
    echo "staging_report=$STAGE_REPORT"
    echo "staging_report_sha256=$(sha256sum "$STAGE_REPORT" | awk '{print $1}')"
    echo "transport_audit_report=$TRANSPORT_AUDIT_REPORT"
    echo "transport_audit_report_sha256=$(sha256sum "$TRANSPORT_AUDIT_REPORT" | awk '{print $1}')"
    echo "handoff_report=$HANDOFF_REPORT"
    echo "handoff_report_sha256=$(sha256sum "$HANDOFF_REPORT" | awk '{print $1}')"
    echo "next_boot_image=exact-u0g-recovery"
    echo "next_boot_expected_recovery_sha256=$EXPECTED_U0G_RECOVERY_SHA256"
    echo "deployment_status=passed"
} | tee "$REPORT"

"$ADB" shell "rm -f '$REMOTE_IMAGE'" >/dev/null 2>&1 || true

echo
echo "A33 userdata rootfs postwrite verification passed."
echo "No userdata rewrite was performed."
echo "Deployment report: $REPORT"
echo "The next step is to flash the exact U0g recovery candidate; do not boot Android."

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

CONFIRMATION="${1:-}"
REQUIRED_CONFIRMATION="ERASE-ANDROID-USERDATA-INSTALL-PMOS"

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
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
EXPECTED_USERDATA_RESOLVED="${EXPECTED_USERDATA_RESOLVED:-/dev/block/sda36}"
EXPECTED_USERDATA_BYTES="${EXPECTED_USERDATA_BYTES:-114240258048}"
TARGET="/dev/block/by-name/userdata"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: this command irreversibly erases Android userdata.

It destroys Android apps, accounts, settings, media, encryption state, and all
other files stored in userdata. cache, super, Android boot, recovery and the GPT
remain untouched by this script.

Run only after the transport-bound final audit passes, using the exact token:

  bash $0 $REQUIRED_CONFIRMATION
EOF
    exit 2
fi

for command in "$ADB" readlink sha256sum stat awk grep date mkdir; do
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
    "$IMAGE_LINK" \
    "$IMAGE_MANIFEST_LINK" \
    "$STAGE_REPORT" \
    "$TRANSPORT_AUDIT_REPORT" \
    "$HANDOFF_REPORT"; do
    [[ -e "$required" ]] || {
        echo "REFUSING: required deployment input is missing: $required" >&2
        exit 1
    }
done

if [[ "$(value "$TRANSPORT_AUDIT_REPORT" transport_final_audit_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" final_chain_audit_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" staging_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" adb_exec_in_required)" != no || \
      "$(value "$TRANSPORT_AUDIT_REPORT" adb_push_full_image)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" adb_exec_out_full_readback)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" private_backup_checksums)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" rescue_assets_status)" != passed || \
      "$(value "$TRANSPORT_AUDIT_REPORT" persistent_phone_partition_writes)" != no ]]; then
    echo "REFUSING: transport-bound final audit did not pass" >&2
    cat "$TRANSPORT_AUDIT_REPORT" >&2
    exit 1
fi

if [[ "$(value "$HANDOFF_REPORT" verification_status)" != passed || \
      "$(value "$HANDOFF_REPORT" cache_partition_required)" != no || \
      "$(value "$HANDOFF_REPORT" pmos_boot_required_before_second_stage)" != no || \
      "$(value "$HANDOFF_REPORT" init_2nd_embedded)" != yes || \
      "$(value "$HANDOFF_REPORT" pmos_root_discovery)" != yes || \
      "$(value "$HANDOFF_REPORT" switch_root_present)" != yes || \
      "$(value "$HANDOFF_REPORT" recovery_sha256)" != "$EXPECTED_U0G_RECOVERY_SHA256" ]]; then
    echo "REFUSING: exact U0g root handoff report did not pass" >&2
    exit 1
fi

IMAGE="$(readlink -f "$IMAGE_LINK" 2>/dev/null || true)"
MANIFEST="$(readlink -f "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$IMAGE" || ! -f "$IMAGE" || -z "$MANIFEST" || ! -f "$MANIFEST" ]]; then
    echo "REFUSING: prepared userdata image or manifest is missing" >&2
    exit 1
fi

IMAGE_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_SIZE="$(stat -Lc '%s' "$IMAGE")"
IMAGE_UUID="$(value "$MANIFEST" root_uuid)"
if [[ "$(value "$MANIFEST" preparation_status)" != passed || \
      "$(value "$MANIFEST" deployment_sha256)" != "$IMAGE_SHA" || \
      "$(value "$MANIFEST" deployment_size)" != "$IMAGE_SIZE" || \
      "$(value "$MANIFEST" root_type)" != ext4 || \
      "$(value "$MANIFEST" root_label)" != pmOS_root || \
      "$(value "$MANIFEST" fstab_root_only)" != yes || \
      "$(value "$MANIFEST" fstab_boot_mount_removed)" != yes || \
      -z "$IMAGE_UUID" ]]; then
    echo "REFUSING: userdata image differs from its validated manifest" >&2
    exit 1
fi
if (( IMAGE_SIZE % 1048576 != 0 )); then
    echo "REFUSING: deployment image size is not an exact MiB multiple" >&2
    exit 1
fi
READBACK_MIB=$((IMAGE_SIZE / 1048576))

REMOTE_IMAGE="$(value "$STAGE_REPORT" remote_image)"
if [[ "$(value "$STAGE_REPORT" staging_status)" != passed || \
      "$(value "$STAGE_REPORT" twrp_recovery_sha256)" != "$KNOWN_TWRP_SHA256" || \
      "$(value "$STAGE_REPORT" source_sha256)" != "$IMAGE_SHA" || \
      "$(value "$STAGE_REPORT" source_size)" != "$IMAGE_SIZE" || \
      "$(value "$STAGE_REPORT" remote_sha256)" != "$IMAGE_SHA" || \
      "$(value "$STAGE_REPORT" remote_size)" != "$IMAGE_SIZE" || \
      "$(value "$STAGE_REPORT" full_readback_sha256)" != "$IMAGE_SHA" || \
      "$(value "$STAGE_REPORT" adb_exec_in_required)" != no || \
      "$(value "$STAGE_REPORT" adb_push_full_image)" != passed || \
      "$(value "$STAGE_REPORT" adb_exec_out_full_readback)" != passed || \
      "$REMOTE_IMAGE" != /tmp/a33x-userdata-pmos-root.img ]]; then
    echo "REFUSING: volatile TWRP staging report did not pass the exact contract" >&2
    cat "$STAGE_REPORT" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"

echo "=== Wait for exact known-good TWRP ==="
a33_init_recovery_adb 30

LIVE_STATE="$(
    "$ADB" shell sh -s -- "$TARGET" "$REMOTE_IMAGE" 2>/dev/null <<'SH' | tr -d '\r'
set -u
target="$1"
remote="$2"
resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"
echo "userdata_resolved=$resolved"
echo "userdata_bytes=$(blockdev --getsize64 "$target" 2>/dev/null || true)"
echo "userdata_readonly=$(blockdev --getro "$target" 2>/dev/null || true)"
echo "remote_present=$([ -f "$remote" ] && echo yes || echo no)"
echo "remote_size=$(stat -c '%s' "$remote" 2>/dev/null || true)"
echo "remote_sha256=$(sha256sum "$remote" 2>/dev/null | awk 'NR==1 {print $1}')"
echo "proc_swaps_readable=$([ -r /proc/swaps ] && echo yes || echo no)"

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

RECOVERY_SHA="$(state_value recovery_sha)"
USERDATA_RESOLVED="$(state_value userdata_resolved)"
USERDATA_BYTES="$(state_value userdata_bytes)"
USERDATA_READONLY="$(state_value userdata_readonly)"
REMOTE_PRESENT="$(state_value remote_present)"
REMOTE_SIZE="$(state_value remote_size)"
REMOTE_SHA="$(state_value remote_sha256)"
PROC_SWAPS_READABLE="$(state_value proc_swaps_readable)"
MOUNT_USERS="$(state_section mount_users)"
SWAP_USERS="$(state_section swap_users)"
DM_USERS="$(state_section dm_users)"

if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" || \
      "$USERDATA_RESOLVED" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$USERDATA_BYTES" != "$EXPECTED_USERDATA_BYTES" || \
      "$USERDATA_READONLY" != 0 || \
      "$REMOTE_PRESENT" != yes || \
      "$REMOTE_SIZE" != "$IMAGE_SIZE" || \
      "$REMOTE_SHA" != "$IMAGE_SHA" || \
      "$PROC_SWAPS_READABLE" != yes || \
      -n "$MOUNT_USERS" || -n "$SWAP_USERS" || -n "$DM_USERS" ]]; then
    echo "REFUSING: live TWRP, userdata, or staged-image state is unsafe" >&2
    printf '%s\n' "$LIVE_STATE" >&2
    exit 1
fi
if (( IMAGE_SIZE >= USERDATA_BYTES )); then
    echo "REFUSING: image does not fit userdata" >&2
    exit 1
fi

cat <<EOF

DESTRUCTIVE WRITE AUTHORIZED
  target:       $TARGET
  resolved:     $USERDATA_RESOLVED
  target bytes: $USERDATA_BYTES
  staged image: $REMOTE_IMAGE
  image bytes:  $IMAGE_SIZE
  image SHA256: $IMAGE_SHA

Only userdata will be overwritten. cache, super, boot, recovery and the GPT are
not touched by this script.
EOF

STARTED="$(date -Ins)"
echo "=== Write the already staged and verified image to userdata ==="
"$ADB" shell sh -s -- "$REMOTE_IMAGE" "$TARGET" <<'SH'
set -eu
source="$1"
target="$2"
dd if="$source" of="$target" bs=1048576
sync
SH

echo "=== Read back the complete written image range ==="
WRITTEN_PREFIX_META="$(
    "$ADB" exec-out sh -c "dd if='$TARGET' bs=1048576 count='$READBACK_MIB' 2>/dev/null" |
        python3 -c 'import hashlib,sys
h=hashlib.sha256()
n=0
while True:
    block=sys.stdin.buffer.read(1024*1024)
    if not block:
        break
    n += len(block)
    h.update(block)
print(n, h.hexdigest())'
)"
WRITTEN_PREFIX_SIZE="$(awk '{print $1}' <<<"$WRITTEN_PREFIX_META")"
WRITTEN_PREFIX_SHA="$(awk '{print $2}' <<<"$WRITTEN_PREFIX_META")"
if [[ "$WRITTEN_PREFIX_SIZE" != "$IMAGE_SIZE" || "$WRITTEN_PREFIX_SHA" != "$IMAGE_SHA" ]]; then
    echo "REFUSING: userdata full-prefix SHA256 mismatch after write" >&2
    echo "expected=$IMAGE_SHA actual=$WRITTEN_PREFIX_SHA" >&2
    exit 1
fi

REMOTE_IDENTITY="$(a33_ext4_identity "$TARGET")"
REMOTE_TYPE="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="type" {print $2; exit}')"
REMOTE_LABEL="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="label" {print $2; exit}')"
REMOTE_UUID="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="uuid" {print $2; exit}')"
if [[ "$REMOTE_TYPE" != ext4 || "$REMOTE_LABEL" != pmOS_root || "$REMOTE_UUID" != "$IMAGE_UUID" ]]; then
    echo "REFUSING: written filesystem identity is wrong" >&2
    echo "$REMOTE_IDENTITY" >&2
    exit 1
fi

VERIFY_OUTPUT="$(
    "$ADB" shell sh -s -- "$TARGET" "$IMAGE_UUID" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
target="$1"
expected_uuid="$2"
mountpoint=/tmp/a33x-userdata-root-verify
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
cleanup() { umount "$mountpoint" 2>/dev/null || true; }
trap cleanup EXIT
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
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
if [[ "$FSTAB_ACTIVE" != "UUID=$IMAGE_UUID / ext4 defaults 0 1" ]]; then
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

"$ADB" shell "rm -f '$REMOTE_IMAGE'" >/dev/null 2>&1 || true
FINISHED="$(date -Ins)"

{
    echo "started=$STARTED"
    echo "finished=$FINISHED"
    echo "operation=destructive-userdata-rootfs-deployment"
    echo "deployment_transport=verified-adb-push-staged-file"
    echo "adb_exec_in_used=no"
    echo "android_userdata_erased=yes"
    echo "cache_written=no"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=no"
    echo "target=$TARGET"
    echo "target_resolved=$USERDATA_RESOLVED"
    echo "target_bytes=$USERDATA_BYTES"
    echo "deployment_image=$IMAGE"
    echo "deployment_size=$IMAGE_SIZE"
    echo "deployment_sha256=$IMAGE_SHA"
    echo "written_prefix_size=$WRITTEN_PREFIX_SIZE"
    echo "written_prefix_sha256=$WRITTEN_PREFIX_SHA"
    echo "filesystem_type=$REMOTE_TYPE"
    echo "filesystem_label=$REMOTE_LABEL"
    echo "filesystem_uuid=$REMOTE_UUID"
    echo "read_only_mount_verification=passed"
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

echo
echo "A33 postmarketOS rootfs was written to userdata and verified."
echo "Android userdata has been erased."
echo "Report: $REPORT"
echo "Do not boot Android. Flash and boot the exact U0g recovery candidate next."
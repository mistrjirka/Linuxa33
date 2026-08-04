#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"
DEPLOY_REPORT="${DEPLOY_REPORT:-$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt}"
CANDIDATE="${CANDIDATE:-$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-recovery.img}"
REPORT="$PORT_ROOT/build/a33-first-rootfs-u0g-flash.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_CANDIDATE_SHA256="e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"
EXPECTED_CANDIDATE_SIZE=100663296
REMOTE_IMAGE="/tmp/a33x-u0g-first-rootfs-recovery.img"
TARGET="/dev/block/by-name/recovery"
USERDATA="/dev/block/by-name/userdata"

for command in "$ADB" sha256sum stat awk grep date mkdir; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

if [[ ! -f "$DEPLOY_REPORT" || "$(value "$DEPLOY_REPORT" deployment_status)" != passed ]]; then
    echo "REFUSING: successful userdata deployment report is missing" >&2
    exit 1
fi
if [[ "$(value "$DEPLOY_REPORT" cache_written)" != no || \
      "$(value "$DEPLOY_REPORT" super_written)" != no || \
      "$(value "$DEPLOY_REPORT" boot_written)" != no || \
      "$(value "$DEPLOY_REPORT" recovery_written)" != no || \
      "$(value "$DEPLOY_REPORT" next_boot_expected_recovery_sha256)" != "$EXPECTED_CANDIDATE_SHA256" ]]; then
    echo "REFUSING: deployment report does not describe the approved isolated layout" >&2
    cat "$DEPLOY_REPORT" >&2
    exit 1
fi

DEPLOYMENT_SHA="$(value "$DEPLOY_REPORT" deployment_sha256)"
DEPLOYMENT_SIZE="$(value "$DEPLOY_REPORT" deployment_size)"
DEPLOYMENT_UUID="$(value "$DEPLOY_REPORT" filesystem_uuid)"
if [[ ! "$DEPLOYMENT_SHA" =~ ^[0-9a-f]{64}$ || \
      ! "$DEPLOYMENT_SIZE" =~ ^[0-9]+$ || \
      -z "$DEPLOYMENT_UUID" || \
      $((DEPLOYMENT_SIZE % 1048576)) -ne 0 ]]; then
    echo "REFUSING: deployment report has invalid rootfs identity" >&2
    exit 1
fi
READBACK_MIB=$((DEPLOYMENT_SIZE / 1048576))

if [[ ! -f "$CANDIDATE" ]]; then
    echo "REFUSING: exact U0g candidate is missing: $CANDIDATE" >&2
    exit 1
fi
CANDIDATE_SHA="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
CANDIDATE_SIZE="$(stat -Lc '%s' "$CANDIDATE")"
if [[ "$CANDIDATE_SHA" != "$EXPECTED_CANDIDATE_SHA256" || \
      "$CANDIDATE_SIZE" != "$EXPECTED_CANDIDATE_SIZE" ]]; then
    echo "REFUSING: local U0g candidate identity mismatch" >&2
    echo "expected_sha=$EXPECTED_CANDIDATE_SHA256 actual_sha=$CANDIDATE_SHA" >&2
    echo "expected_size=$EXPECTED_CANDIDATE_SIZE actual_size=$CANDIDATE_SIZE" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"

echo "=== Wait for exact known-good TWRP ==="
a33_init_recovery_adb 30

LIVE="$(
    "$ADB" shell sh -s -- "$USERDATA" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
target="$1"
resolved="$(readlink -f "$target")"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery | awk 'NR==1 {print $1}')"
echo "userdata_resolved=$resolved"
echo "userdata_readonly=$(blockdev --getro "$target" 2>/dev/null || true)"
echo "mount_users_begin"
awk '{print $1, $2}' /proc/mounts | while read -r source mountpoint; do
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
USERDATA_IDENTITY="$(a33_ext4_identity "$USERDATA")"
LIVE="${LIVE}"$'\n'"userdata_type=$(awk -F= '$1=="type" {print $2; exit}' <<<"$USERDATA_IDENTITY")"
LIVE="${LIVE}"$'\n'"userdata_label=$(awk -F= '$1=="label" {print $2; exit}' <<<"$USERDATA_IDENTITY")"
LIVE="${LIVE}"$'\n'"userdata_uuid=$(awk -F= '$1=="uuid" {print $2; exit}' <<<"$USERDATA_IDENTITY")"

live_value() {
    local key="$1"
    printf '%s\n' "$LIVE" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}
section() {
    local name="$1"
    printf '%s\n' "$LIVE" | awk -v begin="${name}_begin" -v end="${name}_end" '
        $0==begin {inside=1; next}
        $0==end {inside=0}
        inside && NF {print}
    '
}
MOUNTS="$(section mount_users)"
SWAPS="$(section swap_users)"
DM_USERS="$(section dm_users)"

if [[ "$(live_value recovery_sha)" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: recovery is not exact known-good TWRP before candidate flash" >&2
    exit 1
fi
if [[ "$(live_value userdata_resolved)" != /dev/block/sda36 || \
      "$(live_value userdata_readonly)" != 0 || \
      "$(live_value userdata_type)" != ext4 || \
      "$(live_value userdata_label)" != pmOS_root || \
      "$(live_value userdata_uuid)" != "$DEPLOYMENT_UUID" || \
      -n "$MOUNTS" || -n "$SWAPS" || -n "$DM_USERS" ]]; then
    echo "REFUSING: deployed userdata rootfs is wrong, read-only, or still in use" >&2
    printf '%s\n' "$LIVE" >&2
    exit 1
fi

CURRENT_PREFIX_SHA="$(
    "$ADB" exec-out sh -c "dd if='$USERDATA' bs=1048576 count='$READBACK_MIB' 2>/dev/null" \
    | sha256sum \
    | awk '{print $1}'
)"
if [[ "$CURRENT_PREFIX_SHA" != "$DEPLOYMENT_SHA" ]]; then
    echo "REFUSING: userdata rootfs prefix changed after deployment" >&2
    echo "expected=$DEPLOYMENT_SHA actual=$CURRENT_PREFIX_SHA" >&2
    exit 1
fi

echo "=== Upload exact U0g recovery candidate ==="
"$ADB" push "$CANDIDATE" "$REMOTE_IMAGE"
REMOTE_IDENTITY="$(
    "$ADB" shell sh -s -- "$REMOTE_IMAGE" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
image="$1"
echo "size=$(stat -c '%s' "$image")"
echo "sha=$(sha256sum "$image" | awk 'NR==1 {print $1}')"
SH
)"
REMOTE_SIZE="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="size" {print $2; exit}')"
REMOTE_SHA="$(printf '%s\n' "$REMOTE_IDENTITY" | awk -F= '$1=="sha" {print $2; exit}')"
if [[ "$REMOTE_SIZE" != "$EXPECTED_CANDIDATE_SIZE" || "$REMOTE_SHA" != "$EXPECTED_CANDIDATE_SHA256" ]]; then
    "$ADB" shell "rm -f '$REMOTE_IMAGE'" >/dev/null 2>&1 || true
    echo "REFUSING: uploaded U0g candidate identity mismatch" >&2
    exit 1
fi

echo "=== Write only the recovery partition ==="
"$ADB" shell sh -s -- "$REMOTE_IMAGE" "$TARGET" <<'SH'
set -eu
image="$1"
target="$2"
dd if="$image" of="$target" bs=4194304
sync
SH

PARTITION_SHA="$("$ADB" shell "sha256sum '$TARGET'" | awk 'NR==1 {print $1}' | tr -d '\r')"
"$ADB" shell "rm -f '$REMOTE_IMAGE'" >/dev/null 2>&1 || true
if [[ "$PARTITION_SHA" != "$EXPECTED_CANDIDATE_SHA256" ]]; then
    echo "REFUSING: recovery partition hash does not match exact U0g" >&2
    echo "expected=$EXPECTED_CANDIDATE_SHA256 actual=$PARTITION_SHA" >&2
    exit 1
fi

{
    echo "created=$(date -Ins)"
    echo "operation=flash-exact-u0g-after-userdata-deploy"
    echo "deployment_report=$DEPLOY_REPORT"
    echo "deployment_report_sha256=$(sha256sum "$DEPLOY_REPORT" | awk '{print $1}')"
    echo "userdata_prefix_sha256=$CURRENT_PREFIX_SHA"
    echo "candidate=$CANDIDATE"
    echo "candidate_size=$CANDIDATE_SIZE"
    echo "candidate_sha256=$CANDIDATE_SHA"
    echo "recovery_target=$TARGET"
    echo "recovery_partition_sha256=$PARTITION_SHA"
    echo "userdata_written=no"
    echo "cache_written=no"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=yes"
    echo "reboot_performed=no"
    echo "flash_status=passed"
} | tee "$REPORT"

echo
echo "Exact U0g recovery candidate flashed and verified."
echo "Report: $REPORT"
echo "The phone remains in the current TWRP userspace until explicitly rebooted."

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

MANIFEST="${MANIFEST:-$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0h-userdata-root-node-manifest.txt}"
DEPLOY_REPORT="${DEPLOY_REPORT:-$PORT_ROOT/build/a33-userdata-rootfs-deployment.txt}"
REPORT="$PORT_ROOT/build/a33-first-rootfs-u0h-flash.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA_RESOLVED="/dev/block/sda36"
EXPECTED_USERDATA_BYTES="114240258048"
USERDATA="/dev/block/by-name/userdata"
RECOVERY="/dev/block/by-name/recovery"
REMOTE_CANDIDATE="/tmp/a33x-u0h-userdata-root-node-recovery.img"

for command in \
    "$ADB" sha256sum stat awk grep date mkdir debugfs mktemp rm sort sed cat \
    cmp cut tr; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$MANIFEST" "$DEPLOY_REPORT" "$SCRIPT_DIR/lib/a33-adb-runtime.sh"; do
    [[ -f "$required" ]] || {
        echo "Missing required U0h flash input: $required" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

CANDIDATE="$(value "$MANIFEST" recovery)"
CANDIDATE_SHA="$(value "$MANIFEST" recovery_sha256)"
CANDIDATE_SIZE="$(value "$MANIFEST" recovery_size)"
if [[ "$(value "$MANIFEST" candidate)" != U0h-userdata-root-node || \
      "$(value "$MANIFEST" preparation_status)" != passed || \
      "$(value "$MANIFEST" hook_order_validation)" != passed || \
      "$(value "$MANIFEST" hook_before_root_discovery)" != yes || \
      "$(value "$MANIFEST" build_status)" != passed || \
      ! "$CANDIDATE_SHA" =~ ^[0-9a-f]{64}$ || \
      "$CANDIDATE_SIZE" != 100663296 || \
      ! -f "$CANDIDATE" ]]; then
    echo "REFUSING: U0h candidate manifest did not pass" >&2
    cat "$MANIFEST" >&2
    exit 1
fi
if [[ "$(stat -Lc '%s' "$CANDIDATE")" != "$CANDIDATE_SIZE" || \
      "$(sha256sum "$CANDIDATE" | awk '{print $1}')" != "$CANDIDATE_SHA" ]]; then
    echo "REFUSING: U0h candidate differs from its manifest" >&2
    exit 1
fi

if [[ "$(value "$DEPLOY_REPORT" deployment_status)" != passed || \
      "$(value "$DEPLOY_REPORT" filesystem_type)" != ext4 || \
      "$(value "$DEPLOY_REPORT" filesystem_label)" != pmOS_root || \
      "$(value "$DEPLOY_REPORT" cache_written)" != no || \
      "$(value "$DEPLOY_REPORT" super_written)" != no || \
      "$(value "$DEPLOY_REPORT" boot_written)" != no || \
      "$(value "$DEPLOY_REPORT" recovery_written)" != no ]]; then
    echo "REFUSING: verified userdata deployment report is invalid" >&2
    cat "$DEPLOY_REPORT" >&2
    exit 1
fi
ROOT_UUID="$(value "$DEPLOY_REPORT" filesystem_uuid)"
IMAGE="$(value "$DEPLOY_REPORT" deployment_image)"
IMAGE_SHA="$(value "$DEPLOY_REPORT" deployment_sha256)"
IMAGE_SIZE="$(value "$DEPLOY_REPORT" deployment_size)"
if [[ -z "$ROOT_UUID" || ! "$IMAGE_SHA" =~ ^[0-9a-f]{64}$ || \
      ! "$IMAGE_SIZE" =~ ^[0-9]+$ || ! -f "$IMAGE" || \
      "$(stat -Lc '%s' "$IMAGE")" != "$IMAGE_SIZE" || \
      "$(sha256sum "$IMAGE" | awk '{print $1}')" != "$IMAGE_SHA" ]]; then
    echo "REFUSING: local deployment image no longer matches its report" >&2
    exit 1
fi

CRITICAL_PATHS=(
    /bin/busybox
    /usr/sbin/sshd
    /etc/init.d/sshd
    /etc/init.d/networkmanager
    /usr/libexec/a33x-muic-switch-dynamic
    /etc/fstab
    /etc/a33x-rootfs-target
)
TMP="$(mktemp -d)"
EXPECTED_CRITICAL="$TMP/critical-expected.txt"
cleanup_host() { rm -rf "$TMP"; }
trap cleanup_host EXIT
: > "$EXPECTED_CRITICAL"
for path in "${CRITICAL_PATHS[@]}"; do
    output="$TMP/$(printf '%s' "$path" | sed 's#^/##; s#/#__#g')"
    debugfs -R "cat $path" "$IMAGE" > "$output" 2> "$output.stderr" || {
        echo "REFUSING: local deployment image cannot read critical path: $path" >&2
        cat "$output.stderr" >&2 || true
        exit 1
    }
    [[ -s "$output" ]] || {
        echo "REFUSING: local critical path is empty: $path" >&2
        exit 1
    }
    printf '%s %s\n' "$(sha256sum "$output" | awk '{print $1}')" "$path" \
        >> "$EXPECTED_CRITICAL"
done
sort -o "$EXPECTED_CRITICAL" "$EXPECTED_CRITICAL"
EXPECTED_CRITICAL_SHA="$(sha256sum "$EXPECTED_CRITICAL" | awk '{print $1}')"

echo "=== Wait for exact known-good TWRP ==="
a33_init_recovery_adb 30

live_state() {
    "$ADB" shell sh -s -- "$USERDATA" 2>/dev/null <<'SH' | tr -d '\r'
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
        echo "$mountpoint"
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
}
section() {
    local text="$1" name="$2"
    printf '%s\n' "$text" | awk -v begin="${name}_begin" -v end="${name}_end" '
        $0==begin {inside=1; next}
        $0==end {inside=0}
        inside && NF {print}
    '
}
state_value() {
    local text="$1" key="$2"
    printf '%s\n' "$text" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}

LIVE="$(live_state)"
if [[ "$(state_value "$LIVE" recovery_sha)" != "$KNOWN_TWRP_SHA256" || \
      "$(state_value "$LIVE" userdata_resolved)" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$(state_value "$LIVE" userdata_bytes)" != "$EXPECTED_USERDATA_BYTES" || \
      "$(state_value "$LIVE" userdata_readonly)" != 0 || \
      -n "$(section "$LIVE" swap_users)" || \
      -n "$(section "$LIVE" dm_users)" ]]; then
    echo "REFUSING: TWRP or userdata state is unsafe" >&2
    printf '%s\n' "$LIVE" >&2
    exit 1
fi

# TWRP may automatically mount the repurposed userdata filesystem as /data and
# /sdcard. Release only mountpoints proven to resolve to sda36, then require the
# target to remain unused before the read-only verification and recovery flash.
for attempt in 1 2 3; do
    MOUNTS="$(section "$LIVE" mount_users)"
    [[ -z "$MOUNTS" ]] && break
    while IFS= read -r mountpoint; do
        [[ -n "$mountpoint" ]] || continue
        "$ADB" shell "umount '$mountpoint'" >/dev/null 2>&1 || true
    done < <(printf '%s\n' "$MOUNTS" | awk '{print length, $0}' | sort -nr | cut -d' ' -f2-)
    LIVE="$(live_state)"
done
if [[ -n "$(section "$LIVE" mount_users)" || \
      -n "$(section "$LIVE" swap_users)" || \
      -n "$(section "$LIVE" dm_users)" ]]; then
    echo "REFUSING: userdata remains mounted or in use after exact unmount attempts" >&2
    printf '%s\n' "$LIVE" >&2
    exit 1
fi

IDENTITY="$(a33_ext4_identity "$USERDATA")"
if [[ "$(state_value "$IDENTITY" type)" != ext4 || \
      "$(state_value "$IDENTITY" label)" != pmOS_root || \
      "$(state_value "$IDENTITY" uuid)" != "$ROOT_UUID" ]]; then
    echo "REFUSING: installed userdata filesystem identity is wrong" >&2
    printf '%s\n' "$IDENTITY" >&2
    exit 1
fi

echo "=== Verify deployed rootfs read-only against critical local content ==="
VERIFY_OUTPUT="$(
    "$ADB" shell sh -s -- "$USERDATA" "$ROOT_UUID" "${CRITICAL_PATHS[@]}" 2>&1 <<'SH' | tr -d '\r'
set -eu
target="$1"
expected_uuid="$2"
shift 2
mountpoint=/tmp/a33x-u0h-root-verify
mounted=no
cleanup() {
    [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true
}
trap cleanup EXIT
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes

for path in "$@"; do
    [ -f "$mountpoint$path" ] || {
        echo "critical_missing=$path"
        exit 20
    }
    echo "critical_sha=$(sha256sum "$mountpoint$path" | awk '{print $1}') path=$path"
done

for path in /sbin/init /etc/os-release; do
    full="$mountpoint$path"
    if [ -e "$full" ]; then
        :
    elif [ -L "$full" ]; then
        link="$(readlink "$full")"
        case "$link" in
            /*) rooted="$mountpoint$link" ;;
            *) parent="${path%/*}"; rooted="$mountpoint$parent/$link" ;;
        esac
        [ -e "$rooted" ] || [ -L "$rooted" ] || {
            echo "root_symlink_target_missing=$path target=$link"
            exit 21
        }
    else
        echo "root_path_missing=$path"
        exit 22
    fi
done

for pair in \
    /etc/runlevels/default/sshd:/etc/init.d/sshd \
    /etc/runlevels/default/networkmanager:/etc/init.d/networkmanager; do
    path="${pair%%:*}"
    expected="${pair#*:}"
    [ -L "$mountpoint$path" ] || exit 23
    [ "$(readlink "$mountpoint$path")" = "$expected" ] || exit 24
    [ -e "$mountpoint$expected" ] || [ -L "$mountpoint$expected" ] || exit 25
done

active="$(grep -Ev '^[[:space:]]*(#|$)' "$mountpoint/etc/fstab" || true)"
[ "$active" = "UUID=$expected_uuid / ext4 defaults 0 1" ] || {
    echo "fstab_active=$active"
    exit 26
}
grep -Fqx "root_uuid=$expected_uuid" "$mountpoint/etc/a33x-rootfs-target" || exit 27
grep -Fqx 'target=android-userdata' "$mountpoint/etc/a33x-rootfs-target" || exit 28

umount "$mountpoint"
mounted=no
echo "readonly_verification=passed"
echo "readonly_unmount=passed"
SH
)" || {
    rc=$?
    echo "REFUSING: U0h read-only rootfs verification failed (rc=$rc)" >&2
    printf '%s\n' "$VERIFY_OUTPUT" >&2
    exit "$rc"
}
printf '%s\n' "$VERIFY_OUTPUT"
ACTUAL_CRITICAL="$TMP/critical-actual.txt"
printf '%s\n' "$VERIFY_OUTPUT" \
    | awk '$1 ~ /^critical_sha=/ && $2 ~ /^path=/ {
        sub(/^critical_sha=/, "", $1); sub(/^path=/, "", $2); print $1, $2
      }' \
    | sort > "$ACTUAL_CRITICAL"
if ! cmp -s "$EXPECTED_CRITICAL" "$ACTUAL_CRITICAL"; then
    echo "REFUSING: installed rootfs critical content differs from the validated image" >&2
    echo "--- expected ---" >&2
    cat "$EXPECTED_CRITICAL" >&2
    echo "--- actual ---" >&2
    cat "$ACTUAL_CRITICAL" >&2
    exit 1
fi
[[ "$(printf '%s\n' "$VERIFY_OUTPUT" | grep -c '^readonly_verification=passed$')" = 1 && \
   "$(printf '%s\n' "$VERIFY_OUTPUT" | grep -c '^readonly_unmount=passed$')" = 1 ]] || {
    echo "REFUSING: read-only verification did not finish cleanly" >&2
    exit 1
}

LIVE="$(live_state)"
if [[ -n "$(section "$LIVE" mount_users)" || \
      -n "$(section "$LIVE" swap_users)" || \
      -n "$(section "$LIVE" dm_users)" ]]; then
    echo "REFUSING: userdata became active after read-only verification" >&2
    printf '%s\n' "$LIVE" >&2
    exit 1
fi

echo "=== Upload exact U0h recovery candidate ==="
"$ADB" push "$CANDIDATE" "$REMOTE_CANDIDATE"
REMOTE_META="$(
    "$ADB" shell "stat -c '%s' '$REMOTE_CANDIDATE'; sha256sum '$REMOTE_CANDIDATE'" \
        | tr -d '\r'
)"
if [[ "$(sed -n '1p' <<<"$REMOTE_META")" != "$CANDIDATE_SIZE" || \
      "$(awk 'NR==2 {print $1}' <<<"$REMOTE_META")" != "$CANDIDATE_SHA" ]]; then
    "$ADB" shell "rm -f '$REMOTE_CANDIDATE'" >/dev/null 2>&1 || true
    echo "REFUSING: uploaded U0h candidate identity mismatch" >&2
    exit 1
fi

echo "=== Write only the recovery partition ==="
"$ADB" shell sh -s -- "$REMOTE_CANDIDATE" "$RECOVERY" <<'SH'
set -eu
image="$1"
target="$2"
dd if="$image" of="$target" bs=4194304
sync
SH
RECOVERY_SHA="$("$ADB" shell "sha256sum '$RECOVERY'" | awk 'NR==1 {print $1}' | tr -d '\r')"
"$ADB" shell "rm -f '$REMOTE_CANDIDATE'" >/dev/null 2>&1 || true
if [[ "$RECOVERY_SHA" != "$CANDIDATE_SHA" ]]; then
    echo "REFUSING: recovery partition does not match exact U0h" >&2
    echo "expected=$CANDIDATE_SHA actual=$RECOVERY_SHA" >&2
    exit 1
fi

{
    echo "created=$(date -Ins)"
    echo "operation=flash-exact-u0h-userdata-root-node"
    echo "deployment_report=$DEPLOY_REPORT"
    echo "deployment_report_sha256=$(sha256sum "$DEPLOY_REPORT" | awk '{print $1}')"
    echo "userdata_validation=identity-and-critical-content-passed"
    echo "userdata_filesystem_uuid=$ROOT_UUID"
    echo "userdata_critical_manifest_sha256=$EXPECTED_CRITICAL_SHA"
    echo "candidate_manifest=$MANIFEST"
    echo "candidate_manifest_sha256=$(sha256sum "$MANIFEST" | awk '{print $1}')"
    echo "candidate=$CANDIDATE"
    echo "candidate_size=$CANDIDATE_SIZE"
    echo "candidate_sha256=$CANDIDATE_SHA"
    echo "recovery_target=$RECOVERY"
    echo "recovery_partition_sha256=$RECOVERY_SHA"
    echo "userdata_written=no"
    echo "cache_written=no"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=yes"
    echo "reboot_performed=no"
    echo "flash_status=passed"
} | tee "$REPORT"

cleanup_host
trap - EXIT

echo
echo "Exact U0h recovery candidate flashed and verified."
echo "Report: $REPORT"
echo "The phone remains in TWRP until the U0h observer explicitly reboots it."

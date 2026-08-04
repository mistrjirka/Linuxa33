#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$SCRIPT_DIR/collect-a33-previous-boot.sh"
EXPECTED_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
TARGET="/dev/block/by-name/userdata"
EXPECTED_TARGET="/dev/block/sda36"
U0H_METADATA_RELATIVE="a33x-bringup/u0h-root-node-result.txt"

# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"

for command in "$ADB" bash find sort awk grep tar sha256sum date cp stat python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
[[ -f "$BASE" ]] || {
    echo "Missing previous-boot collector: $BASE" >&2
    exit 1
}

echo "=== Require exact known-good TWRP ==="
a33_init_recovery_adb 30
RECOVERY_SHA="$("$ADB" shell 'sha256sum /dev/block/by-name/recovery' | awk 'NR==1 {print $1}' | tr -d '\r')"
if [[ "$RECOVERY_SHA" != "$EXPECTED_TWRP_SHA256" ]]; then
    echo "REFUSING: exact known-good TWRP is not running" >&2
    echo "expected=$EXPECTED_TWRP_SHA256 actual=${RECOVERY_SHA:-missing}" >&2
    exit 1
fi

echo "=== Capture U0h previous boot and persistent hook result ==="
METADATA_RESULT_RELATIVE="$U0H_METADATA_RELATIVE" \
EXPECTED_TWRP_SHA256="$EXPECTED_TWRP_SHA256" \
    bash "$BASE" u0h-root-node

OUT="$(
    find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'u0h-root-node-result-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
if [[ -z "$OUT" || ! -d "$OUT" ]]; then
    echo "REFUSING: U0h result directory was not found" >&2
    exit 1
fi

if [[ -f "$OUT/u0f-metadata-result.txt" ]]; then
    cp -a "$OUT/u0f-metadata-result.txt" "$OUT/u0h-root-node-metadata-result.txt"
fi

echo "=== Release only TWRP mounts backed by userdata ==="
UNMOUNT_STATE="$(
    "$ADB" shell sh -s -- "$TARGET" "$EXPECTED_TARGET" 2>&1 <<'SH' | tr -d '\r'
set -eu
target="$1"
expected="$2"
resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target_resolved=$resolved"
[ "$resolved" = "$expected" ] || exit 20

source_for_mount() {
    awk -v mp="$1" '$2==mp {print $1; exit}' /proc/mounts 2>/dev/null || true
}

for mp in /sdcard /data; do
    source="$(source_for_mount "$mp")"
    [ -n "$source" ] || continue
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    case "$mp" in
        /data)
            if [ "$source" != "$target" ] && [ "$source" != "$resolved" ] && [ "$source_resolved" != "$resolved" ]; then
                echo "refusing_unrelated_mount=$mp source=$source resolved=$source_resolved"
                exit 21
            fi
            ;;
        /sdcard)
            # /sdcard may be a bind mount under /data; unmount it before /data.
            ;;
    esac
    umount "$mp" 2>/dev/null || true
done

# Release any remaining direct mount of sda36, deepest paths first by repeating.
for pass in 1 2 3; do
    changed=no
    awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mp; do
        source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
        if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
            umount "$mp" 2>/dev/null || true
        fi
    done
    [ "$changed" = no ] || true
done

echo "remaining_mounts_begin"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mp; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$source $mp"
    fi
done
echo "remaining_mounts_end"
SH
)" || {
    rc=$?
    echo "REFUSING: exact userdata unmount step failed (rc=$rc)" >&2
    printf '%s\n' "$UNMOUNT_STATE" >&2
    exit "$rc"
}
printf '%s\n' "$UNMOUNT_STATE" > "$OUT/twrp-userdata-unmount-state.txt"
if awk '/^remaining_mounts_begin$/ {inside=1; next} /^remaining_mounts_end$/ {inside=0} inside && NF {found=1} END {exit found ? 0 : 1}' \
    "$OUT/twrp-userdata-unmount-state.txt"; then
    echo "REFUSING: userdata remains mounted in TWRP" >&2
    cat "$OUT/twrp-userdata-unmount-state.txt" >&2
    exit 1
fi

echo "=== Inspect installed rootfs read-only ==="
"$ADB" shell sh -s -- "$TARGET" > "$OUT/u0h-rootfs-readonly-state.txt" <<'SH'
set -eu
target="$1"
mountpoint=/tmp/a33x-u0h-rootfs-collect
mounted=no
cleanup() {
    [ "$mounted" = no ] || umount "$mountpoint" 2>/dev/null || true
}
trap cleanup EXIT

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
echo "readonly_mount=passed"

root_path_state() {
    path="$1"
    full="$mountpoint$path"
    if [ -e "$full" ]; then
        echo "path_present=$path"
        [ ! -L "$full" ] || echo "path_link=$path->$(readlink "$full" 2>/dev/null || true)"
        return
    fi
    if [ -L "$full" ]; then
        link="$(readlink "$full" 2>/dev/null || true)"
        echo "path_symlink=$path->$link"
        case "$link" in
            /*) rooted="$mountpoint$link" ;;
            *) parent="${path%/*}"; [ "$parent" = "$path" ] && parent=/; rooted="$mountpoint$parent/$link" ;;
        esac
        if [ -e "$rooted" ] || [ -L "$rooted" ]; then
            echo "path_symlink_target_present=$path"
        else
            echo "path_symlink_target_missing=$path target=$link"
        fi
        return
    fi
    echo "path_missing=$path"
}

for path in \
    /sbin/init \
    /etc/os-release \
    /usr/sbin/sshd \
    /etc/init.d/sshd \
    /etc/init.d/networkmanager \
    /etc/runlevels/default/sshd \
    /etc/runlevels/default/networkmanager \
    /etc/fstab \
    /etc/a33x-rootfs-target; do
    root_path_state "$path"
done

echo "ssh_directory_begin"
ls -la "$mountpoint/etc/ssh" 2>&1 || true
echo "ssh_directory_end"

echo "ssh_host_keys_begin"
for key in "$mountpoint"/etc/ssh/ssh_host_*; do
    [ -f "$key" ] || continue
    echo "host_key path=${key#$mountpoint} bytes=$(stat -c '%s' "$key" 2>/dev/null || true) mtime=$(stat -c '%Y' "$key" 2>/dev/null || true)"
done
echo "ssh_host_keys_end"

echo "runlevel_default_begin"
ls -la "$mountpoint/etc/runlevels/default" 2>&1 || true
echo "runlevel_default_end"

echo "var_log_listing_begin"
find "$mountpoint/var/log" -mindepth 1 -maxdepth 2 -printf '%y %s %T@ %p\n' 2>/dev/null | sed "s#$mountpoint##" | sort || true
echo "var_log_listing_end"

for relative in \
    /var/log/messages \
    /var/log/boot \
    /var/log/boot.log \
    /var/log/rc.log \
    /var/log/dmesg \
    /var/log/daemon.log \
    /var/log/auth.log \
    /var/log/secure; do
    file="$mountpoint$relative"
    if [ -f "$file" ]; then
        echo "log_begin=$relative"
        tail -n 400 "$file" 2>&1 || true
        echo "log_end=$relative"
    fi
done

echo "filesystem_usage_begin"
df -h "$mountpoint" 2>&1 || true
echo "filesystem_usage_end"

umount "$mountpoint"
mounted=no
echo "readonly_unmount=passed"
SH

SANITIZED="$OUT/last_kmsg.sanitized.txt"
if [[ -f "$SANITIZED" ]]; then
    grep -aEin \
        'a33x-root-node|u0h|pmOS_root|wait_root_partition|find_root_partition|resize2fs|e2fsck|mount.*root|sysroot|switch_root|OpenRC|openrc|sshd|ssh-keygen|NetworkManager|networkmanager|EXT4-fs|VFS:|Kernel panic|panic - not syncing|Call trace|BUG:|Oops|Unable to handle|dwc3|gadget' \
        "$SANITIZED" > "$OUT/u0h-focused-last-kmsg.txt" || true
fi

LATEST_OBSERVATION="$(
    find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'a33-first-rootfs-observation-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
if [[ -n "$LATEST_OBSERVATION" && -d "$LATEST_OBSERVATION" ]]; then
    mkdir -p "$OUT/observation"
    cp -a "$LATEST_OBSERVATION"/. "$OUT/observation/"
fi

METADATA_STATUS="$(awk -F= '$1=="metadata_result_status" {print $2; exit}' "$OUT/u0h-root-node-metadata-result.txt" 2>/dev/null || true)"
HOOK_RESULT="$(awk -F= '$1=="result" {print $2; exit}' "$OUT/u0h-root-node-metadata-result.txt" 2>/dev/null || true)"
HOOK_REASON="$(awk -F= '$1=="reason" {print $2; exit}' "$OUT/u0h-root-node-metadata-result.txt" 2>/dev/null || true)"
HOST_KEY_COUNT="$(grep -c '^host_key path=' "$OUT/u0h-rootfs-readonly-state.txt" 2>/dev/null || true)"
SWITCH_ROOT_COUNT="$(grep -aEic 'switch_root|sysroot' "$OUT/u0h-focused-last-kmsg.txt" 2>/dev/null || true)"
OPENRC_COUNT="$(grep -aEic 'OpenRC|openrc' "$OUT/u0h-focused-last-kmsg.txt" 2>/dev/null || true)"
SSHD_COUNT="$(grep -aEic 'sshd|ssh-keygen' "$OUT/u0h-focused-last-kmsg.txt" 2>/dev/null || true)"

{
    echo "created=$(date -Ins)"
    echo "operation=collect-u0h-previous-boot"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "u0h_metadata_status=${METADATA_STATUS:-unknown}"
    echo "u0h_hook_result=${HOOK_RESULT:-unknown}"
    echo "u0h_hook_reason=${HOOK_REASON:-unknown}"
    echo "ssh_host_key_count=$HOST_KEY_COUNT"
    echo "switch_root_log_count=$SWITCH_ROOT_COUNT"
    echo "openrc_log_count=$OPENRC_COUNT"
    echo "sshd_log_count=$SSHD_COUNT"
    echo "phone_partition_writes=no"
    echo "collection_status=passed"
} | tee "$OUT/u0h-summary.txt"

ARCHIVE="$OUT.tar.gz"
tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "U0h previous-boot evidence collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
cat "$OUT/u0h-summary.txt"

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$SCRIPT_DIR/collect-a33-first-rootfs-previous-boot.sh"
EXPECTED_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
TARGET="/dev/block/by-name/userdata"
DEPLOYED_IMAGE_BYTES=802160640

# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"

for command in "$ADB" bash find sort awk grep tar sha256sum date python3 stat; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
[[ -f "$BASE" ]] || {
    echo "Missing base first-rootfs collector: $BASE" >&2
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

echo "=== Run proven previous-boot and readonly-rootfs collector ==="
bash "$BASE"

OUT="$(
    find "$RESULT_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'first-rootfs-result-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
if [[ -z "$OUT" || ! -d "$OUT" ]]; then
    echo "REFUSING: latest first-rootfs result directory was not found" >&2
    exit 1
fi

SUPERBLOCK="$OUT/ext4-superblock-state-v3.txt"
ROOT_STATE="$OUT/rootfs-openrc-ssh-state-v3.txt"
FOCUSED="$OUT/first-rootfs-focused-last-kmsg-v3.txt"
SUMMARY="$OUT/first-rootfs-failure-v3-summary.txt"

echo "=== Capture ext4 superblock state and resize evidence ==="
"$ADB" exec-out sh -c "dd if='$TARGET' bs=2048 count=1 2>/dev/null" |
    python3 -c '
import datetime as dt
import struct
import sys

image_bytes = int(sys.argv[1])
data = sys.stdin.buffer.read()
if len(data) != 2048:
    raise SystemExit(f"expected 2048 bytes, received {len(data)}")
sb = data[1024:2048]
if sb[0x38:0x3a] != b"\x53\xef":
    raise SystemExit("ext superblock magic mismatch")

def u16(off):
    return struct.unpack_from("<H", sb, off)[0]

def u32(off):
    return struct.unpack_from("<I", sb, off)[0]

block_size = 1024 << u32(0x18)
feature_incompat = u32(0x60)
blocks = u32(0x04)
if feature_incompat & 0x80:
    blocks |= u32(0x150) << 32
filesystem_bytes = blocks * block_size
mtime = u32(0x2c)
wtime = u32(0x30)
last_mounted = sb[0x88:0xc8].split(b"\x00", 1)[0].decode("ascii", "replace")

def iso(ts):
    if ts == 0:
        return "never"
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat()

print("superblock_magic=ef53")
print(f"block_size={block_size}")
print(f"block_count={blocks}")
print(f"filesystem_bytes={filesystem_bytes}")
print(f"deployed_image_bytes={image_bytes}")
print(f"filesystem_expanded_beyond_image={\"yes\" if filesystem_bytes > image_bytes else \"no\"}")
print(f"mount_count={u16(0x34)}")
print(f"filesystem_state=0x{u16(0x3a):04x}")
print(f"last_mount_time_utc={iso(mtime)}")
print(f"last_write_time_utc={iso(wtime)}")
print(f"last_mounted_path={last_mounted or \"empty\"}")
' "$DEPLOYED_IMAGE_BYTES" > "$SUPERBLOCK"

echo "=== Mount userdata read-only and inspect rootfs/OpenRC/SSH state ==="
"$ADB" shell sh -s -- "$TARGET" > "$ROOT_STATE" <<'SH'
set -u
target="$1"
mountpoint=/tmp/a33x-first-rootfs-v3-check
mounted_here=no
cleanup() {
    if [ "$mounted_here" = yes ]; then
        umount "$mountpoint" 2>/dev/null || true
    fi
}
trap cleanup EXIT

umount "$mountpoint" 2>/dev/null || true
mkdir -p "$mountpoint"
if ! mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"; then
    echo "readonly_mount=failed"
    dmesg 2>/dev/null | tail -n 100 || true
    exit 20
fi
mounted_here=yes
echo "readonly_mount=passed"

root_path_state() {
    path="$1"
    full="$mountpoint$path"
    if [ -e "$full" ]; then
        echo "path_present=$path"
        if [ -L "$full" ]; then
            echo "path_link=$path->$(readlink "$full" 2>/dev/null || true)"
        fi
        return 0
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
        return 0
    fi
    echo "path_missing=$path"
}

for path in \
    /sbin/init \
    /etc/os-release \
    /etc/fstab \
    /etc/a33x-rootfs-target \
    /usr/sbin/sshd \
    /etc/init.d/sshd \
    /etc/init.d/networkmanager \
    /etc/runlevels/default/sshd \
    /etc/runlevels/default/networkmanager \
    /usr/libexec/a33x-muic-switch-dynamic; do
    root_path_state "$path"
done

echo "runlevel_default_begin"
ls -la "$mountpoint/etc/runlevels/default" 2>&1 || true
echo "runlevel_default_end"

echo "init_links_begin"
ls -la "$mountpoint/sbin/init" "$mountpoint/etc/os-release" 2>&1 || true
echo "init_links_end"

echo "fstab_begin"
cat "$mountpoint/etc/fstab" 2>&1 || true
echo "fstab_end"

echo "rootfs_marker_begin"
cat "$mountpoint/etc/a33x-rootfs-target" 2>&1 || true
echo "rootfs_marker_end"

echo "sshd_config_active_begin"
grep -Ev '^[[:space:]]*(#|$)' "$mountpoint/etc/ssh/sshd_config" 2>&1 || true
echo "sshd_config_active_end"

echo "ssh_directory_begin"
ls -la "$mountpoint/etc/ssh" 2>&1 || true
echo "ssh_directory_end"

echo "ssh_host_key_metadata_begin"
for key in "$mountpoint"/etc/ssh/ssh_host_*; do
    [ -e "$key" ] || continue
    base="${key#$mountpoint}"
    if [ -f "$key" ]; then
        echo "host_key path=$base bytes=$(stat -c '%s' "$key" 2>/dev/null || true) mtime=$(stat -c '%Y' "$key" 2>/dev/null || true)"
    else
        echo "host_key_nonregular path=$base"
    fi
done
echo "ssh_host_key_metadata_end"

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
        tail -n 300 "$file" 2>&1 || true
        echo "log_end=$relative"
    fi
done

echo "root_directory_timestamps_begin"
for path in / /etc /etc/ssh /var /var/log /home /root; do
    full="$mountpoint$path"
    if [ -e "$full" ]; then
        echo "stat path=$path mode=$(stat -c '%a' "$full" 2>/dev/null || true) mtime=$(stat -c '%Y' "$full" 2>/dev/null || true)"
    fi
done
echo "root_directory_timestamps_end"

df -h "$mountpoint" 2>&1 || true
if umount "$mountpoint"; then
    mounted_here=no
    echo "readonly_unmount=passed"
else
    echo "readonly_unmount=failed"
    exit 21
fi
SH

echo "=== Extract first-rootfs handoff and service evidence from last_kmsg ==="
SANITIZED="$OUT/last_kmsg.sanitized.txt"
if [[ -f "$SANITIZED" ]]; then
    PATTERN='init_2nd|jump_init_2nd|pmOS_root|root_wait|root_resize|resize2fs|e2fsck|fsck|mount.*root|sysroot|switch_root|OpenRC|openrc|sshd|ssh-keygen|NetworkManager|networkmanager|a33x-(watchdog|usbpd|muic-switch|muic-persist)|dwc3|gadget|Kernel panic|panic - not syncing|Call trace|BUG:|Oops|Unable to handle|EXT4-fs|VFS:'
    grep -aEin "$PATTERN" "$SANITIZED" > "$FOCUSED" || true
else
    echo "last_kmsg_sanitized_missing=yes" > "$FOCUSED"
fi

value_from() {
    local key="$1" file="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file" 2>/dev/null || true
}
count_in() {
    local pattern="$1" file="$2"
    grep -aEic "$pattern" "$file" 2>/dev/null || true
}
HOST_KEY_COUNT="$(grep -c '^host_key path=' "$ROOT_STATE" 2>/dev/null || true)"

{
    echo "created=$(date -Ins)"
    echo "operation=collect-first-rootfs-ssh-failure-v3"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "filesystem_bytes=$(value_from filesystem_bytes "$SUPERBLOCK")"
    echo "filesystem_expanded_beyond_image=$(value_from filesystem_expanded_beyond_image "$SUPERBLOCK")"
    echo "filesystem_mount_count=$(value_from mount_count "$SUPERBLOCK")"
    echo "filesystem_last_mount_time_utc=$(value_from last_mount_time_utc "$SUPERBLOCK")"
    echo "filesystem_last_write_time_utc=$(value_from last_write_time_utc "$SUPERBLOCK")"
    echo "filesystem_last_mounted_path=$(value_from last_mounted_path "$SUPERBLOCK")"
    echo "readonly_mount=$(value_from readonly_mount "$ROOT_STATE")"
    echo "readonly_unmount=$(value_from readonly_unmount "$ROOT_STATE")"
    echo "ssh_host_key_count=$HOST_KEY_COUNT"
    echo "root_resize_log_count=$(count_in 'root_resize|resize2fs' "$FOCUSED")"
    echo "root_mount_log_count=$(count_in 'pmOS_root|mount.*root|sysroot|EXT4-fs' "$FOCUSED")"
    echo "switch_root_log_count=$(count_in 'switch_root|init_2nd' "$FOCUSED")"
    echo "openrc_log_count=$(count_in 'OpenRC|openrc' "$FOCUSED")"
    echo "sshd_log_count=$(count_in 'sshd|ssh-keygen' "$FOCUSED")"
    echo "kernel_panic_log_count=$(count_in 'Kernel panic|panic - not syncing' "$FOCUSED")"
    echo "phone_partition_writes=no"
    echo "collection_status=passed"
} | tee "$SUMMARY"

ARCHIVE="$OUT.tar.gz"
tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "First-rootfs SSH failure evidence v3 collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Summary:"
cat "$SUMMARY"

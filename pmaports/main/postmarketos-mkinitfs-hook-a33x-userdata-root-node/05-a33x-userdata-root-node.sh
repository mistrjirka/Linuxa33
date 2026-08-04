#!/bin/sh

# A33 first real-rootfs bridge:
# Samsung's downstream UFS partitions appear in sysfs, but the minimal
# initramfs did not create /dev nodes for them. postmarketOS root discovery
# uses blkid, so create and verify only the already-proven userdata node.

status=/run/a33x-userdata-root-node-status.txt
root_name=sda36
root_sys=/sys/class/block/$root_name
root_dev=/dev/$root_name
root_block_dev=/dev/block/$root_name
root_by_name=/dev/block/by-name/userdata
expected_sectors=223125504
expected_label=pmOS_root
metadata_name=sda26
metadata_mount=/run/a33x-root-node-metadata
metadata_relative=a33x-bringup/u0h-root-node-result.txt

ensure_kmsg() {
    if [ ! -c /dev/kmsg ]; then
        mkdir -p /dev
        mknod /dev/kmsg c 1 11 2>/dev/null || true
    fi
}

log_root_node() {
    message="a33x-root-node-v1: $*"
    ensure_kmsg
    if [ -w /dev/kmsg ]; then
        printf '<6>%s\n' "$message" > /dev/kmsg 2>/dev/null || true
    fi
    printf '%s\n' "$message"
}

record() {
    printf '%s=%s\n' "$1" "$2" >> "$status"
}

finish() {
    result="$1"
    reason="$2"
    record result "$result"
    record reason "$reason"
    log_root_node "result=$result reason=$reason"
    persist_status || true
}

valid_devnum() {
    case "$1" in
        ''|*[!0-9:]*|:*|*:)
            return 1
            ;;
    esac
    [ "${1#*:}" != "$1" ]
}

create_block_node() {
    path="$1"
    major="$2"
    minor="$3"
    mkdir -p "${path%/*}"
    if [ -e "$path" ] || [ -L "$path" ]; then
        [ -b "$path" ] || return 1
        return 0
    fi
    mknod "$path" b "$major" "$minor"
    [ -b "$path" ]
}

create_from_sysfs() {
    name="$1"
    output="$2"
    sys=/sys/class/block/$name
    [ -r "$sys/dev" ] || return 1
    devnum="$(cat "$sys/dev" 2>/dev/null || true)"
    valid_devnum "$devnum" || return 1
    major="${devnum%%:*}"
    minor="${devnum##*:}"
    create_block_node "$output" "$major" "$minor"
}

persist_status() {
    [ -s "$status" ] || return 1

    metadata_device=""
    for candidate in /dev/block/by-name/metadata /dev/block/sda26 /dev/sda26; do
        if [ -b "$candidate" ]; then
            metadata_device="$candidate"
            break
        fi
    done
    if [ -z "$metadata_device" ]; then
        create_from_sysfs "$metadata_name" /dev/block/sda26 || return 1
        metadata_device=/dev/block/sda26
    fi

    resolved="$(readlink -f "$metadata_device" 2>/dev/null || true)"
    existing="$(awk -v a="$metadata_device" -v b="$resolved" '$1==a || $1==b {print $2; exit}' /proc/mounts 2>/dev/null || true)"
    mounted_here=no
    if [ -n "$existing" ]; then
        root="$existing"
    else
        mkdir -p "$metadata_mount"
        umount "$metadata_mount" 2>/dev/null || true
        mount -t ext4 -o rw,nosuid,nodev,noatime "$metadata_device" "$metadata_mount" || return 1
        root="$metadata_mount"
        mounted_here=yes
    fi

    result="$root/$metadata_relative"
    temporary="$result.tmp"
    mkdir -p "${result%/*}" || {
        [ "$mounted_here" = no ] || umount "$metadata_mount" 2>/dev/null || true
        return 1
    }
    cat "$status" > "$temporary" || {
        rm -f "$temporary"
        [ "$mounted_here" = no ] || umount "$metadata_mount" 2>/dev/null || true
        return 1
    }
    chmod 0600 "$temporary" 2>/dev/null || true
    sync
    mv -f "$temporary" "$result" || {
        rm -f "$temporary"
        [ "$mounted_here" = no ] || umount "$metadata_mount" 2>/dev/null || true
        return 1
    }
    sync
    if [ "$mounted_here" = yes ]; then
        umount "$metadata_mount" || return 1
    fi
    log_root_node "persisted=/$metadata_relative"
}

mkdir -p /run /dev /dev/block /dev/block/by-name
: > "$status"
record candidate U0h-userdata-root-node
record expected_sysfs "$root_sys"
record expected_sectors "$expected_sectors"
record expected_label "$expected_label"
record expected_resolved "$root_block_dev"

attempt=0
while [ "$attempt" -lt 100 ]; do
    if [ -r "$root_sys/dev" ] && [ -r "$root_sys/size" ]; then
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.1
done
record wait_attempts "$attempt"

if [ ! -r "$root_sys/dev" ] || [ ! -r "$root_sys/size" ]; then
    finish failed root-sysfs-missing
    return 0 2>/dev/null || exit 0
fi

devnum="$(cat "$root_sys/dev" 2>/dev/null || true)"
sectors="$(cat "$root_sys/size" 2>/dev/null || true)"
record sysfs_devnum "${devnum:-missing}"
record sysfs_sectors "${sectors:-missing}"
if ! valid_devnum "$devnum"; then
    finish failed invalid-root-devnum
    return 0 2>/dev/null || exit 0
fi
if [ "$sectors" != "$expected_sectors" ]; then
    finish failed unexpected-root-size
    return 0 2>/dev/null || exit 0
fi

major="${devnum%%:*}"
minor="${devnum##*:}"
if ! create_block_node "$root_dev" "$major" "$minor"; then
    finish failed create-top-level-node
    return 0 2>/dev/null || exit 0
fi
if ! create_block_node "$root_block_dev" "$major" "$minor"; then
    finish failed create-block-node
    return 0 2>/dev/null || exit 0
fi
if ! ln -sfn ../sda36 "$root_by_name"; then
    finish failed create-by-name-link
    return 0 2>/dev/null || exit 0
fi

resolved_top="$(readlink -f "$root_dev" 2>/dev/null || true)"
resolved_block="$(readlink -f "$root_block_dev" 2>/dev/null || true)"
resolved_by_name="$(readlink -f "$root_by_name" 2>/dev/null || true)"
record root_dev "$root_dev"
record root_block_dev "$root_block_dev"
record root_by_name "$root_by_name"
record resolved_top "${resolved_top:-missing}"
record resolved_block "${resolved_block:-missing}"
record resolved_by_name "${resolved_by_name:-missing}"
if [ "$resolved_by_name" != "$root_block_dev" ]; then
    finish failed by-name-resolution-mismatch
    return 0 2>/dev/null || exit 0
fi

if ! command -v blkid >/dev/null 2>&1; then
    finish failed blkid-missing
    return 0 2>/dev/null || exit 0
fi
identity="$(blkid "$root_block_dev" 2>/dev/null || true)"
record blkid_output "${identity:-missing}"
case "$identity" in
    *'TYPE="ext4"'*) ;;
    *)
        finish failed root-type-not-ext4
        return 0 2>/dev/null || exit 0
        ;;
esac
case "$identity" in
    *'LABEL="pmOS_root"'*) ;;
    *)
        finish failed root-label-not-pmOS_root
        return 0 2>/dev/null || exit 0
        ;;
esac

uuid="$(printf '%s\n' "$identity" | sed -n 's/.*UUID="\([^"]*\)".*/\1/p')"
record root_uuid "${uuid:-missing}"
record root_node_created yes
finish passed verified-userdata-root-node
return 0 2>/dev/null || exit 0

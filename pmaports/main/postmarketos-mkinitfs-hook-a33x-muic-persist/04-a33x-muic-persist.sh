#!/bin/sh

# U0f observability-only overlay:
# - run after the unchanged U0e MUIC helper hook;
# - inspect its RAM transcript and the resulting I2C state;
# - persist the exact result on the Android metadata filesystem;
# - do not perform any MUIC register access or alter the USB role path.

ensure_kmsg() {
    if [ ! -c /dev/kmsg ]; then
        mkdir -p /dev
        mknod /dev/kmsg c 1 11 2>/dev/null || true
    fi
}

log_u0f() {
    message="a33x-muic-persist-v1: $*"
    ensure_kmsg
    if [ -w /dev/kmsg ]; then
        printf '<6>%s\n' "$message" > /dev/kmsg 2>/dev/null || true
    fi
    printf '%s\n' "$message"
}

safe_value() {
    value="$1"
    if [ -n "$value" ]; then
        printf '%s' "$value" | tr '\n\r' '  '
    else
        printf 'none'
    fi
}

create_metadata_node_from_sysfs() {
    [ -r /sys/class/block/sda26/dev ] || return 1

    devnum="$(cat /sys/class/block/sda26/dev 2>/dev/null || true)"
    major="${devnum%%:*}"
    minor="${devnum##*:}"
    case "$major:$minor" in
        *[!0-9:]*|:|*:)
            return 1
            ;;
    esac

    mkdir -p /dev/block
    mknod /dev/block/sda26 b "$major" "$minor" 2>/dev/null || true
    [ -b /dev/block/sda26 ]
}

mkdir -p /run /dev
ensure_kmsg

report=/run/a33x-u0f-muic-result.txt
helper_output=/run/a33x-muic-switch-helper.log
insmod_error=/run/a33x-muic-switch-insmod.err
mountpoint=/run/a33x-metadata
relative_dir=a33x-bringup
relative_file=u0f-muic-result.txt

: > "$report"
{
    echo "candidate=U0f-muic-persist"
    echo "observer_version=1"
    echo "functional_base=U0e-muic-switch"
    echo "functional_delta=none"
    echo "observability_delta=metadata_persistent_result"
    echo "metadata_partition=/dev/block/sda26"
    echo "metadata_result=/$relative_dir/$relative_file"
    echo "boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo unavailable)"
    echo "kernel=$(uname -r 2>/dev/null || echo unavailable)"
    echo "uptime=$(cat /proc/uptime 2>/dev/null || echo unavailable)"

    if [ -d /sys/module/i2c_dev ]; then
        echo "i2c_dev_loaded=yes"
    else
        echo "i2c_dev_loaded=no"
    fi

    adapter_target="$(readlink /sys/class/i2c-dev/i2c-2 2>/dev/null || true)"
    adapter_dev="$(cat /sys/class/i2c-dev/i2c-2/dev 2>/dev/null || true)"
    echo "i2c2_adapter_target=$(safe_value "$adapter_target")"
    echo "i2c2_device_number=$(safe_value "$adapter_dev")"

    if [ -c /dev/i2c-2 ]; then
        echo "i2c2_character_device=yes"
        ls -l /dev/i2c-2 2>/dev/null | sed 's/^/i2c2_character_device_ls=/' || true
    else
        echo "i2c2_character_device=no"
    fi

    if [ -e /sys/bus/i2c/devices/2-003e ]; then
        owner_name="$(cat /sys/bus/i2c/devices/2-003e/name 2>/dev/null || true)"
        owner_driver="$(readlink /sys/bus/i2c/devices/2-003e/driver 2>/dev/null || true)"
        echo "address_2_003e_owned=yes"
        echo "address_2_003e_name=$(safe_value "$owner_name")"
        echo "address_2_003e_driver=$(safe_value "$owner_driver")"
    else
        echo "address_2_003e_owned=no"
    fi

    if [ -f "$helper_output" ]; then
        echo "helper_output_present=yes"
        if grep -q 'a33x-muic-switch-v1: success ctrl1=0x17 switch=0x24' "$helper_output"; then
            echo "helper_success_marker=yes"
        else
            echo "helper_success_marker=no"
        fi
        if grep -Eq 'a33x-muic-switch-v1: ERROR|rollback-(ok|failed)' "$helper_output"; then
            echo "helper_error_or_rollback_marker=yes"
        else
            echo "helper_error_or_rollback_marker=no"
        fi
        echo "helper_output_begin"
        cat "$helper_output"
        echo "helper_output_end"
    else
        echo "helper_output_present=no"
        echo "helper_success_marker=no"
        echo "helper_error_or_rollback_marker=unknown"
    fi

    if [ -f "$insmod_error" ]; then
        echo "insmod_error_present=yes"
        echo "insmod_error_begin"
        cat "$insmod_error"
        echo "insmod_error_end"
    else
        echo "insmod_error_present=no"
    fi

    echo "focused_dmesg_begin"
    dmesg 2>/dev/null | grep -Ei 'a33x|s2mu106|usbpd|pdic|muic|i2c|dwc3|gadget|USB_ATTACH_UFP|reserve_state|runtime_resume|runtime_suspend|conndone|connect.done|reset|panic' | tail -n 240 || true
    echo "focused_dmesg_end"
} >> "$report"

log_u0f "RAM report ready path=$report"

metadata_device=""
metadata_node_created=no
attempt=0
while [ "$attempt" -lt 50 ]; do
    for candidate in /dev/block/by-name/metadata /dev/block/sda26 /dev/sda26; do
        if [ -b "$candidate" ]; then
            metadata_device="$candidate"
            break
        fi
    done
    [ -n "$metadata_device" ] && break

    if create_metadata_node_from_sysfs; then
        metadata_device=/dev/block/sda26
        metadata_node_created=yes
        log_u0f "created metadata block node from sysfs path=$metadata_device"
        break
    fi

    attempt=$((attempt + 1))
    sleep 0.1
done

if [ -z "$metadata_device" ]; then
    log_u0f "ERROR metadata block device missing after=${attempt}x100ms"
    return 0 2>/dev/null || exit 0
fi

resolved_device="$(readlink -f "$metadata_device" 2>/dev/null || true)"
if grep -qE "^(${metadata_device}|${resolved_device}) " /proc/mounts 2>/dev/null; then
    log_u0f "ERROR metadata already mounted device=$metadata_device resolved=${resolved_device:-unknown}"
    return 0 2>/dev/null || exit 0
fi

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
if ! mount -t ext4 -o rw,nosuid,nodev,noatime "$metadata_device" "$mountpoint"; then
    log_u0f "ERROR metadata mount failed device=$metadata_device"
    return 0 2>/dev/null || exit 0
fi

result_dir="$mountpoint/$relative_dir"
result_file="$result_dir/$relative_file"
temporary_file="$result_file.tmp"

if ! mkdir -p "$result_dir"; then
    log_u0f "ERROR result directory creation failed path=$result_dir"
    umount "$mountpoint" 2>/dev/null || true
    return 0 2>/dev/null || exit 0
fi
chmod 0700 "$result_dir" 2>/dev/null || true

{
    echo "metadata_device=$metadata_device"
    echo "metadata_resolved=${resolved_device:-unknown}"
    echo "metadata_node_created=$metadata_node_created"
    echo "metadata_mount=rw"
} >> "$report"

if ! cat "$report" > "$temporary_file"; then
    log_u0f "ERROR result write failed path=$temporary_file"
    rm -f "$temporary_file"
    umount "$mountpoint" 2>/dev/null || true
    return 0 2>/dev/null || exit 0
fi
chmod 0600 "$temporary_file" 2>/dev/null || true
sync

if ! mv -f "$temporary_file" "$result_file"; then
    log_u0f "ERROR atomic result rename failed path=$result_file"
    rm -f "$temporary_file"
    umount "$mountpoint" 2>/dev/null || true
    return 0 2>/dev/null || exit 0
fi
sync

persisted_size="$(wc -c < "$result_file" 2>/dev/null || true)"
log_u0f "result persisted path=/$relative_dir/$relative_file bytes=${persisted_size:-unknown}"

if ! umount "$mountpoint"; then
    log_u0f "ERROR metadata unmount failed path=$mountpoint"
    return 0 2>/dev/null || exit 0
fi

log_u0f "success metadata result synchronized and unmounted"
return 0 2>/dev/null || exit 0

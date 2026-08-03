#!/bin/sh

# U0g persistence overlay:
# - run after the dynamic MUIC switch hook;
# - persist selected physical controller/runtime bus, exact hook status and
#   helper transcript to Android metadata;
# - perform no I2C register access and make no USB role changes.

ensure_kmsg() {
    if [ ! -c /dev/kmsg ]; then
        mkdir -p /dev
        mknod /dev/kmsg c 1 11 2>/dev/null || true
    fi
}

log_u0g() {
    message="a33x-muic-persist-v2: $*"
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

value_from_file() {
    key="$1"
    file="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key) + 2); exit}' "$file" 2>/dev/null || true
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

report=/run/a33x-u0g-muic-result.txt
helper_output=/run/a33x-muic-switch-helper.log
hook_status=/run/a33x-muic-switch-status.txt
selection_file=/run/a33x-muic-switch-selection.env
insmod_error=/run/a33x-muic-switch-insmod.err
mountpoint=/run/a33x-metadata
relative_dir=a33x-bringup
relative_file=u0g-muic-result.txt
controller=13860000.hsi2c

selected_bus="$(value_from_file selected_bus "$selection_file")"
selected_target="$(value_from_file selected_target "$selection_file")"
selected_device="$(value_from_file selected_device "$selection_file")"
selected_device_number="$(value_from_file selected_device_number "$selection_file")"
selected_address_sysfs="$(value_from_file selected_address_sysfs "$selection_file")"
helper_rc="$(value_from_file helper_rc "$selection_file")"

: > "$report"
{
    echo "candidate=U0g-muic-dynamic"
    echo "observer_version=2"
    echo "functional_base=U0f-muic-persist"
    echo "functional_delta=dynamic_runtime_bus_for_13860000_hsi2c"
    echo "observability_delta=metadata_dynamic_selection_result"
    echo "expected_controller=$controller"
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

    if [ -f "$selection_file" ]; then
        echo "selection_file_present=yes"
        echo "selection_begin"
        cat "$selection_file"
        echo "selection_end"
    else
        echo "selection_file_present=no"
    fi

    echo "selected_bus=$(safe_value "$selected_bus")"
    echo "selected_target=$(safe_value "$selected_target")"
    echo "selected_device=$(safe_value "$selected_device")"
    echo "selected_device_number=$(safe_value "$selected_device_number")"
    echo "selected_address_sysfs=$(safe_value "$selected_address_sysfs")"
    echo "helper_rc=$(safe_value "$helper_rc")"

    if [ -n "$selected_device" ] && [ -c "$selected_device" ]; then
        echo "selected_character_device=yes"
        ls -l "$selected_device" 2>/dev/null | sed 's/^/selected_character_device_ls=/' || true
    else
        echo "selected_character_device=no"
    fi

    if [ -n "$selected_address_sysfs" ] && [ -e "$selected_address_sysfs" ]; then
        owner_name="$(cat "$selected_address_sysfs/name" 2>/dev/null || true)"
        owner_driver="$(readlink -f "$selected_address_sysfs/driver" 2>/dev/null || true)"
        echo "selected_address_owned=yes"
        echo "selected_address_name=$(safe_value "$owner_name")"
        echo "selected_address_driver=$(safe_value "$owner_driver")"
    else
        echo "selected_address_owned=no"
    fi

    echo "observed_adapters_begin"
    for entry in /sys/class/i2c-dev/i2c-*; do
        [ -e "$entry" ] || continue
        target="$(readlink -f "$entry" 2>/dev/null || true)"
        devnum="$(cat "$entry/dev" 2>/dev/null || true)"
        echo "adapter entry=$entry target=${target:-unknown} dev=${devnum:-unknown}"
    done
    echo "observed_adapters_end"

    if [ -f "$hook_status" ]; then
        echo "hook_status_present=yes"
        echo "hook_status_begin"
        cat "$hook_status"
        echo "hook_status_end"
    else
        echo "hook_status_present=no"
    fi

    if [ -f "$helper_output" ]; then
        echo "helper_output_present=yes"
        if grep -Eq 'a33x-muic-switch-v2: success device=/dev/i2c-[0-9]+ bus=[0-9]+ ctrl1=0x17 switch=0x24' "$helper_output"; then
            echo "helper_success_marker=yes"
        else
            echo "helper_success_marker=no"
        fi
        if grep -Eq 'a33x-muic-switch-v2: ERROR|rollback-(ok|failed)' "$helper_output"; then
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
    dmesg 2>/dev/null | grep -Ei 'a33x|13860000|s2mu106|usbpd|pdic|muic|i2c|dwc3|gadget|USB_ATTACH_UFP|reserve_state|runtime_resume|runtime_suspend|conndone|connect.done|reset|panic' | tail -n 300 || true
    echo "focused_dmesg_end"
} >> "$report"

log_u0g "RAM report ready path=$report"

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
        log_u0g "created metadata block node from sysfs path=$metadata_device"
        break
    fi

    attempt=$((attempt + 1))
    sleep 0.1
done

if [ -z "$metadata_device" ]; then
    log_u0g "ERROR metadata block device missing after=${attempt}x100ms"
    return 0 2>/dev/null || exit 0
fi

resolved_device="$(readlink -f "$metadata_device" 2>/dev/null || true)"
if grep -qE "^(${metadata_device}|${resolved_device}) " /proc/mounts 2>/dev/null; then
    log_u0g "ERROR metadata already mounted device=$metadata_device resolved=${resolved_device:-unknown}"
    return 0 2>/dev/null || exit 0
fi

mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
if ! mount -t ext4 -o rw,nosuid,nodev,noatime "$metadata_device" "$mountpoint"; then
    log_u0g "ERROR metadata mount failed device=$metadata_device"
    return 0 2>/dev/null || exit 0
fi

result_dir="$mountpoint/$relative_dir"
result_file="$result_dir/$relative_file"
temporary_file="$result_file.tmp"

if ! mkdir -p "$result_dir"; then
    log_u0g "ERROR result directory creation failed path=$result_dir"
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
    log_u0g "ERROR result write failed path=$temporary_file"
    rm -f "$temporary_file"
    umount "$mountpoint" 2>/dev/null || true
    return 0 2>/dev/null || exit 0
fi
chmod 0600 "$temporary_file" 2>/dev/null || true
sync

if ! mv -f "$temporary_file" "$result_file"; then
    log_u0g "ERROR atomic result rename failed path=$result_file"
    rm -f "$temporary_file"
    umount "$mountpoint" 2>/dev/null || true
    return 0 2>/dev/null || exit 0
fi
sync

persisted_size="$(wc -c < "$result_file" 2>/dev/null || true)"
log_u0g "result persisted path=/$relative_dir/$relative_file bytes=${persisted_size:-unknown}"

if ! umount "$mountpoint"; then
    log_u0g "ERROR metadata unmount failed path=$mountpoint"
    return 0 2>/dev/null || exit 0
fi

log_u0g "success metadata result synchronized and unmounted"
return 0 2>/dev/null || exit 0

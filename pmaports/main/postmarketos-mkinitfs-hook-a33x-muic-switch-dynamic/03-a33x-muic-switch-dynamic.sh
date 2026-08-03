#!/bin/sh

# U0g isolated functional correction:
# - retain U0d's Type-C/PDIC path and U0e's exact MUIC register sequence;
# - load only i2c_dev;
# - discover the runtime I2C bus for physical controller 13860000.hsi2c;
# - never assume that Linux bus number 2 is stable across boot environments;
# - do not load the full MUIC/CPIF/BTS dependency closure.

ensure_kmsg() {
    if [ ! -c /dev/kmsg ]; then
        mkdir -p /dev
        mknod /dev/kmsg c 1 11 2>/dev/null || true
    fi
}

log_a33x_muic() {
    message="a33x-muic-switch-v2: $*"
    ensure_kmsg
    if [ -w /dev/kmsg ]; then
        printf '<6>%s\n' "$message" > /dev/kmsg 2>/dev/null || true
    fi
    printf '%s\n' "$message"
}

module_name_from_path() {
    module_basename="${1##*/}"
    case "$module_basename" in
        *.zst) module_basename="${module_basename%.zst}" ;;
    esac
    case "$module_basename" in
        *.xz) module_basename="${module_basename%.xz}" ;;
    esac
    case "$module_basename" in
        *.gz) module_basename="${module_basename%.gz}" ;;
    esac
    module_basename="${module_basename%.ko}"
    printf '%s\n' "$module_basename" | tr '-' '_'
}

module_is_loaded() {
    [ -d "/sys/module/$1" ]
}

load_module_path() {
    local module_path module_name dependency_line dependencies dependency
    local full_path rc error_text

    module_path="$1"
    module_name="$(module_name_from_path "$module_path")"

    if module_is_loaded "$module_name"; then
        log_a33x_muic "already-loaded module=$module_name"
        return 0
    fi

    case " $load_stack " in
        *" $module_name "*)
            log_a33x_muic "ERROR dependency-cycle module=$module_name"
            return 1
            ;;
    esac
    load_stack="$load_stack $module_name"

    dependency_line="$(grep -F "$module_path:" "$modules_dep" 2>/dev/null | head -n 1)"
    if [ -z "$dependency_line" ]; then
        log_a33x_muic "ERROR modules.dep-entry-missing module=$module_name path=$module_path"
        return 1
    fi

    dependencies="${dependency_line#*:}"
    for dependency in $dependencies; do
        load_module_path "$dependency" || return 1
    done

    full_path="$module_dir/$module_path"
    if [ ! -f "$full_path" ]; then
        log_a33x_muic "ERROR module-file-missing module=$module_name path=$full_path"
        return 1
    fi

    log_a33x_muic "insmod-begin module=$module_name path=$module_path"
    : > "$error_file"
    if insmod "$full_path" 2>"$error_file"; then
        log_a33x_muic "insmod-ok module=$module_name"
        return 0
    else
        rc=$?
    fi

    if module_is_loaded "$module_name"; then
        log_a33x_muic "insmod-raced module=$module_name rc=$rc"
        return 0
    fi

    error_text="$(tr '\n' ' ' < "$error_file" 2>/dev/null || true)"
    log_a33x_muic "ERROR insmod-failed module=$module_name rc=$rc detail=${error_text:-none}"
    return "$rc"
}

find_module_path() {
    target_name="$1"
    while IFS=: read -r candidate_path candidate_dependencies; do
        candidate_name="$(module_name_from_path "$candidate_path")"
        if [ "$candidate_name" = "$target_name" ]; then
            printf '%s\n' "$candidate_path"
            return 0
        fi
    done < "$modules_dep"
    return 1
}

mkdir -p /run /dev
ensure_kmsg

module_dir="/usr/lib/modules/$(uname -r)"
modules_dep="$module_dir/modules.dep"
error_file="/run/a33x-muic-switch-insmod.err"
helper_output="/run/a33x-muic-switch-helper.log"
selection_file="/run/a33x-muic-switch-selection.env"
load_stack=""
helper="/usr/libexec/a33x-muic-switch-dynamic"
controller="13860000.hsi2c"
address_hex="003e"

log_a33x_muic "begin controller=$controller address=0x3e discovery=physical-path"

if [ ! -r "$modules_dep" ]; then
    log_a33x_muic "ERROR modules.dep-missing path=$modules_dep"
    return 0 2>/dev/null || exit 0
fi
if ! command -v insmod >/dev/null 2>&1; then
    log_a33x_muic "ERROR insmod-command-missing"
    return 0 2>/dev/null || exit 0
fi
if [ ! -x "$helper" ]; then
    log_a33x_muic "ERROR helper-missing path=$helper"
    return 0 2>/dev/null || exit 0
fi

if ! module_is_loaded i2c_dev; then
    i2c_dev_path="$(find_module_path i2c_dev 2>/dev/null || true)"
    if [ -z "$i2c_dev_path" ]; then
        log_a33x_muic "ERROR i2c-dev-module-absent"
        return 0 2>/dev/null || exit 0
    fi
    if ! load_module_path "$i2c_dev_path"; then
        log_a33x_muic "ERROR i2c-dev-activation-failed"
        return 0 2>/dev/null || exit 0
    fi
fi

selected_entry=""
selected_target=""
selected_bus=""
attempt=0
while [ "$attempt" -lt 50 ]; do
    match_count=0
    candidate_entry=""
    candidate_target=""

    for entry in /sys/class/i2c-dev/i2c-*; do
        [ -e "$entry" ] || continue
        target="$(readlink -f "$entry" 2>/dev/null || true)"
        case "$target" in
            *"/$controller/"*)
                match_count=$((match_count + 1))
                candidate_entry="$entry"
                candidate_target="$target"
                ;;
        esac
    done

    if [ "$match_count" -eq 1 ]; then
        selected_entry="$candidate_entry"
        selected_target="$candidate_target"
        selected_bus="${selected_entry##*-}"
        break
    fi
    if [ "$match_count" -gt 1 ]; then
        log_a33x_muic "ERROR multiple-controller-matches controller=$controller count=$match_count"
        return 0 2>/dev/null || exit 0
    fi

    attempt=$((attempt + 1))
    sleep 0.1
done

if [ -z "$selected_entry" ] || [ -z "$selected_bus" ]; then
    log_a33x_muic "ERROR controller-not-found controller=$controller after=${attempt}x100ms"
    for entry in /sys/class/i2c-dev/i2c-*; do
        [ -e "$entry" ] || continue
        target="$(readlink -f "$entry" 2>/dev/null || true)"
        log_a33x_muic "observed-adapter entry=$entry target=${target:-unknown}"
    done
    return 0 2>/dev/null || exit 0
fi

case "$selected_bus" in
    ''|*[!0-9]*)
        log_a33x_muic "ERROR invalid-selected-bus value=${selected_bus:-empty}"
        return 0 2>/dev/null || exit 0
        ;;
esac

selected_sysfs="/sys/class/i2c-dev/i2c-$selected_bus"
selected_device="/dev/i2c-$selected_bus"
address_device="/sys/bus/i2c/devices/$selected_bus-$address_hex"

log_a33x_muic "controller-selected controller=$controller bus=$selected_bus target=$selected_target"

if [ -e "$address_device" ]; then
    owner_name="$(cat "$address_device/name" 2>/dev/null || true)"
    owner_driver="$(readlink -f "$address_device/driver" 2>/dev/null || true)"
    log_a33x_muic "ERROR address-owned device=$selected_bus-$address_hex name=${owner_name:-unknown} driver=${owner_driver:-none}"
    return 0 2>/dev/null || exit 0
fi

if [ ! -r "$selected_sysfs/dev" ]; then
    log_a33x_muic "ERROR i2c-char-device-sysfs-missing path=$selected_sysfs/dev"
    return 0 2>/dev/null || exit 0
fi

if [ ! -c "$selected_device" ]; then
    devnum="$(cat "$selected_sysfs/dev" 2>/dev/null || true)"
    major="${devnum%%:*}"
    minor="${devnum##*:}"
    case "$major:$minor" in
        *[!0-9:]*|:|*:)
            log_a33x_muic "ERROR invalid-device-number value=${devnum:-empty}"
            return 0 2>/dev/null || exit 0
            ;;
    esac
    if ! mknod "$selected_device" c "$major" "$minor" 2>/dev/null; then
        log_a33x_muic "ERROR mknod-failed path=$selected_device dev=$devnum"
        return 0 2>/dev/null || exit 0
    fi
    chmod 0600 "$selected_device" 2>/dev/null || true
    log_a33x_muic "device-node-created path=$selected_device dev=$devnum"
else
    devnum="$(cat "$selected_sysfs/dev" 2>/dev/null || true)"
    log_a33x_muic "device-node-existing path=$selected_device expected-dev=${devnum:-unknown}"
fi

if [ -e "$address_device" ]; then
    log_a33x_muic "ERROR address-became-owned device=$selected_bus-$address_hex"
    return 0 2>/dev/null || exit 0
fi

{
    echo "selection_version=1"
    echo "selected_controller=$controller"
    echo "selected_bus=$selected_bus"
    echo "selected_entry=$selected_entry"
    echo "selected_target=$selected_target"
    echo "selected_device=$selected_device"
    echo "selected_device_number=${devnum:-unknown}"
    echo "selected_address=0x3e"
    echo "selected_address_sysfs=$address_device"
} > "$selection_file"

: > "$helper_output"
if "$helper" "$selected_device" >"$helper_output" 2>&1; then
    helper_rc=0
else
    helper_rc=$?
fi
printf 'helper_rc=%s\n' "$helper_rc" >> "$selection_file"

while IFS= read -r line; do
    [ -n "$line" ] && log_a33x_muic "helper: $line"
done < "$helper_output"

if [ "$helper_rc" -ne 0 ]; then
    log_a33x_muic "ERROR helper-failed rc=$helper_rc bus=$selected_bus device=$selected_device"
    return 0 2>/dev/null || exit 0
fi

success_marker="a33x-muic-switch-v2: success device=$selected_device bus=$selected_bus ctrl1=0x17 switch=0x24"
if ! grep -Fq "$success_marker" "$helper_output"; then
    log_a33x_muic "ERROR success-marker-missing bus=$selected_bus device=$selected_device"
    return 0 2>/dev/null || exit 0
fi

log_a33x_muic "success controller=$controller bus=$selected_bus device=$selected_device address=0x3e ctrl1=0x17 switch=0x24"
return 0 2>/dev/null || exit 0

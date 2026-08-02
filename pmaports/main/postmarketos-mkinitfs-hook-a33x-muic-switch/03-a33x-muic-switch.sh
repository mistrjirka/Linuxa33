#!/bin/sh

# U0e isolated diagnostic:
# - retain U0d's real-PDIC UFP notification path;
# - load only i2c_dev when needed;
# - route the S2MU106 MUIC D+/D- switch to USB at bus 2, address 0x3e;
# - do not load the full MUIC/CPIF/BTS dependency closure.

ensure_kmsg() {
    if [ ! -c /dev/kmsg ]; then
        mkdir -p /dev
        mknod /dev/kmsg c 1 11 2>/dev/null || true
    fi
}

log_a33x_muic() {
    message="a33x-muic-switch-v1: $*"
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

return_success() {
    return 0 2>/dev/null || exit 0
}

mkdir -p /run /dev
ensure_kmsg

module_dir="/usr/lib/modules/$(uname -r)"
modules_dep="$module_dir/modules.dep"
error_file="/run/a33x-muic-switch-insmod.err"
helper_output="/run/a33x-muic-switch-helper.log"
load_stack=""
helper="/usr/libexec/a33x-muic-switch"

log_a33x_muic "begin bus=2 address=0x3e expected-adapter=13860000.hsi2c"

if [ ! -r "$modules_dep" ]; then
    log_a33x_muic "ERROR modules.dep-missing path=$modules_dep"
    return_success
fi
if ! command -v insmod >/dev/null 2>&1; then
    log_a33x_muic "ERROR insmod-command-missing"
    return_success
fi
if [ ! -x "$helper" ]; then
    log_a33x_muic "ERROR helper-missing path=$helper"
    return_success
fi

# A driver-created 2-003e client means another kernel owner controls the MUIC
# bank. Never force userspace access in that state.
if [ -e /sys/bus/i2c/devices/2-003e ]; then
    owner_name="$(cat /sys/bus/i2c/devices/2-003e/name 2>/dev/null || true)"
    owner_driver="$(readlink /sys/bus/i2c/devices/2-003e/driver 2>/dev/null || true)"
    log_a33x_muic "ERROR address-owned device=2-003e name=${owner_name:-unknown} driver=${owner_driver:-none}"
    return_success
fi

if ! module_is_loaded i2c_dev; then
    i2c_dev_path="$(find_module_path i2c_dev 2>/dev/null || true)"
    if [ -z "$i2c_dev_path" ]; then
        log_a33x_muic "ERROR i2c-dev-module-absent"
        return_success
    fi
    if ! load_module_path "$i2c_dev_path"; then
        log_a33x_muic "ERROR i2c-dev-activation-failed"
        return_success
    fi
fi

attempt=0
while [ "$attempt" -lt 50 ]; do
    [ -r /sys/class/i2c-dev/i2c-2/dev ] && break
    attempt=$((attempt + 1))
    sleep 0.1
done

if [ ! -r /sys/class/i2c-dev/i2c-2/dev ]; then
    log_a33x_muic "ERROR i2c-char-device-sysfs-missing after=${attempt}x100ms"
    return_success
fi

adapter_target="$(readlink /sys/class/i2c-dev/i2c-2 2>/dev/null || true)"
case "$adapter_target" in
    *13860000.hsi2c*)
        log_a33x_muic "adapter-verified target=$adapter_target"
        ;;
    *)
        log_a33x_muic "ERROR unexpected-adapter target=${adapter_target:-unknown}"
        return_success
        ;;
esac

if [ ! -c /dev/i2c-2 ]; then
    devnum="$(cat /sys/class/i2c-dev/i2c-2/dev 2>/dev/null || true)"
    major="${devnum%%:*}"
    minor="${devnum##*:}"
    case "$major:$minor" in
        *[!0-9:]*|:|*:)
            log_a33x_muic "ERROR invalid-device-number value=${devnum:-empty}"
            return_success
            ;;
    esac
    if ! mknod /dev/i2c-2 c "$major" "$minor" 2>/dev/null; then
        log_a33x_muic "ERROR mknod-failed path=/dev/i2c-2 dev=$devnum"
        return_success
    fi
    chmod 0600 /dev/i2c-2 2>/dev/null || true
    log_a33x_muic "device-node-created path=/dev/i2c-2 dev=$devnum"
fi

if [ -e /sys/bus/i2c/devices/2-003e ]; then
    log_a33x_muic "ERROR address-became-owned device=2-003e"
    return_success
fi

: > "$helper_output"
if "$helper" >"$helper_output" 2>&1; then
    helper_rc=0
else
    helper_rc=$?
fi

while IFS= read -r line; do
    [ -n "$line" ] && log_a33x_muic "helper: $line"
done < "$helper_output"

if [ "$helper_rc" -ne 0 ]; then
    log_a33x_muic "ERROR helper-failed rc=$helper_rc"
    return_success
fi

if ! grep -q 'a33x-muic-switch-v1: success ctrl1=0x17 switch=0x24' "$helper_output"; then
    log_a33x_muic "ERROR success-marker-missing"
    return_success
fi

log_a33x_muic "success bus=2 address=0x3e ctrl1=0x17 switch=0x24"
return_success

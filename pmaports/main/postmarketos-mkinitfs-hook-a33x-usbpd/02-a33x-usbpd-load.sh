#!/bin/sh

# activation-contract: s2mu106_usbpd explicit-insmod
# activation-loader: recursive-harddeps-from-modules.dep
#
# The vendor module declares a soft dependency on muic_s2mu106, whose closure
# pulls modem/CPIF drivers into the initramfs. For the isolated USB-PD test,
# resolve only the hard dependency graph from modules.dep and insert each
# module directly. This intentionally does not process modules.softdep.

ensure_kmsg() {
	if [ ! -c /dev/kmsg ]; then
		mkdir -p /dev
		mknod /dev/kmsg c 1 11 2>/dev/null || true
	fi
}

log_a33x_usbpd() {
	message="a33x-usbpd-load-v1: $*"
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
		log_a33x_usbpd "already-loaded module=$module_name"
		return 0
	fi

	case " $load_stack " in
		*" $module_name "*)
			log_a33x_usbpd "ERROR dependency-cycle module=$module_name"
			return 1
			;;
	esac
	load_stack="$load_stack $module_name"

	dependency_line="$(grep -F "$module_path:" "$modules_dep" 2>/dev/null | head -n 1)"
	if [ -z "$dependency_line" ]; then
		log_a33x_usbpd "ERROR modules.dep entry missing module=$module_name path=$module_path"
		return 1
	fi

	dependencies="${dependency_line#*:}"
	for dependency in $dependencies; do
		load_module_path "$dependency" || return 1
	done

	full_path="$module_dir/$module_path"
	if [ ! -f "$full_path" ]; then
		log_a33x_usbpd "ERROR module file missing module=$module_name path=$full_path"
		return 1
	fi

	log_a33x_usbpd "insmod-begin module=$module_name path=$module_path"
	: > "$error_file"
	if insmod "$full_path" 2>"$error_file"; then
		log_a33x_usbpd "insmod-ok module=$module_name"
		return 0
	fi

	rc=$?
	if module_is_loaded "$module_name"; then
		log_a33x_usbpd "insmod-raced module=$module_name rc=$rc"
		return 0
	fi

	error_text="$(tr '\n' ' ' < "$error_file" 2>/dev/null || true)"
	log_a33x_usbpd "ERROR insmod-failed module=$module_name rc=$rc detail=${error_text:-none}"
	return "$rc"
}

mkdir -p /run
ensure_kmsg

target_module="s2mu106_usbpd"
module_dir="/usr/lib/modules/$(uname -r)"
modules_dep="$module_dir/modules.dep"
error_file="/run/a33x-usbpd-insmod.err"
load_stack=""

if ! command -v insmod >/dev/null 2>&1; then
	log_a33x_usbpd "ERROR insmod command missing"
	return 0 2>/dev/null || exit 0
fi

if [ ! -r "$modules_dep" ]; then
	log_a33x_usbpd "ERROR modules.dep missing path=$modules_dep"
	return 0 2>/dev/null || exit 0
fi

target_path=""
while IFS=: read -r candidate_path candidate_dependencies; do
	candidate_name="$(module_name_from_path "$candidate_path")"
	if [ "$candidate_name" = "$target_module" ]; then
		target_path="$candidate_path"
		break
	fi
done < "$modules_dep"

if [ -z "$target_path" ]; then
	log_a33x_usbpd "ERROR target module absent from modules.dep module=$target_module"
	return 0 2>/dev/null || exit 0
fi

log_a33x_usbpd "begin module=$target_module target=$target_path"

if ! load_module_path "$target_path"; then
	log_a33x_usbpd "ERROR activation failed module=$target_module"
	return 0 2>/dev/null || exit 0
fi

if ! module_is_loaded "$target_module"; then
	log_a33x_usbpd "ERROR target absent from sysfs after insmod module=$target_module"
	return 0 2>/dev/null || exit 0
fi

log_a33x_usbpd "module-active module=$target_module"

attempt=0
driver_dir=""
while [ "$attempt" -lt 10 ]; do
	for candidate in \
		/sys/bus/i2c/drivers/usbpd-s2mu106 \
		/sys/bus/i2c/drivers/s2mu106-usbpd
	do
		if [ -d "$candidate" ]; then
			driver_dir="$candidate"
			break
		fi
	done
	[ -n "$driver_dir" ] && break
	attempt=$((attempt + 1))
	sleep 1
done

if [ -z "$driver_dir" ]; then
	log_a33x_usbpd "ERROR i2c driver directory missing after ${attempt}s"
	return 0 2>/dev/null || exit 0
fi

bound_devices=""
for entry in "$driver_dir"/*-*; do
	[ -L "$entry" ] || continue
	bound_devices="$bound_devices ${entry##*/}"
done

log_a33x_usbpd "driver-registered path=$driver_dir bound=${bound_devices:-none}"
return 0 2>/dev/null || exit 0

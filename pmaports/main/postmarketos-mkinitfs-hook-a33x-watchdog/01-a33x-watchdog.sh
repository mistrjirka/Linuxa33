#!/bin/sh

# Samsung's bootloader leaves the cluster-0 watchdog running. TWRP feeds the
# same hardware through /dev/watchdog, but postmarketOS/mdev may only create
# /dev/watchdog0. Resolve the exact CL0 watchdog from sysfs, create its node
# when necessary, then keep it open and feed it until userspace takes over.

ensure_kmsg() {
	if [ ! -c /dev/kmsg ]; then
		mknod /dev/kmsg c 1 11 2>/dev/null || true
	fi
}

log_a33x_watchdog() {
	message="a33x-watchdog-v2: $*"
	ensure_kmsg
	if [ -w /dev/kmsg ]; then
		printf '<6>%s\n' "$message" > /dev/kmsg 2>/dev/null || true
	fi
	printf '%s\n' "$message"
}

create_watchdog0_from_sysfs() {
	[ -r /sys/class/watchdog/watchdog0/dev ] || return 1

	devnum="$(cat /sys/class/watchdog/watchdog0/dev 2>/dev/null)" || return 1
	major="${devnum%:*}"
	minor="${devnum#*:}"

	case "$major" in
		''|*[!0-9]*) return 1 ;;
	esac
	case "$minor" in
		''|*[!0-9]*) return 1 ;;
	esac

	mkdir -p /dev
	mknod /dev/watchdog0 c "$major" "$minor" 2>/dev/null || true
	[ -c /dev/watchdog0 ]
}

mkdir -p /dev /run
ensure_kmsg

watchdog_device=""
attempt=0

while [ "$attempt" -lt 10 ]; do
	if [ -c /dev/watchdog0 ]; then
		watchdog_device=/dev/watchdog0
		break
	fi

	if create_watchdog0_from_sysfs; then
		watchdog_device=/dev/watchdog0
		log_a33x_watchdog "created /dev/watchdog0 from sysfs"
		break
	fi

	# Android/TWRP also provides this legacy alias. Use it only as a fallback.
	if [ -c /dev/watchdog ]; then
		watchdog_device=/dev/watchdog
		break
	fi

	attempt=$((attempt + 1))
	sleep 1
done

if [ -z "$watchdog_device" ]; then
	sysfs_dev="$(cat /sys/class/watchdog/watchdog0/dev 2>/dev/null || true)"
	log_a33x_watchdog "ERROR: no CL0 watchdog node after ${attempt}s; sysfs_dev=${sysfs_dev:-missing}"
	return 0 2>/dev/null || exit 0
fi

(
	if ! exec 3>"$watchdog_device"; then
		log_a33x_watchdog "ERROR: failed to open $watchdog_device"
		exit 1
	fi

	log_a33x_watchdog "opened $watchdog_device; feeding every 8 seconds"

	ping_count=0
	while printf 'K' >&3; do
		ping_count=$((ping_count + 1))
		log_a33x_watchdog "ping=$ping_count device=$watchdog_device"
		sleep 8
	done

	log_a33x_watchdog "ERROR: watchdog write failed"
) &

a33x_watchdog_pid=$!
printf '%s\n' "$a33x_watchdog_pid" > /run/a33x-watchdog.pid
log_a33x_watchdog "feeder pid=$a33x_watchdog_pid device=$watchdog_device"

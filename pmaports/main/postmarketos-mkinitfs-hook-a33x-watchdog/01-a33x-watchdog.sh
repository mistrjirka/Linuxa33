#!/bin/sh

# Samsung's bootloader leaves the cluster watchdog running. TWRP starts
# /system/bin/watchdogd early, but the postmarketOS initramfs does not. Keep
# /dev/watchdog open and ping it until userspace takes over.

log_a33x_watchdog() {
	message="a33x-watchdog: $*"
	if [ -w /dev/kmsg ]; then
		printf '<6>%s\n' "$message" > /dev/kmsg 2>/dev/null || true
	fi
	printf '%s\n' "$message"
}

watchdog_device=/dev/watchdog
attempt=0

while [ "$attempt" -lt 5 ] && [ ! -c "$watchdog_device" ]; do
	attempt=$((attempt + 1))
	sleep 1
done

if [ ! -c "$watchdog_device" ]; then
	log_a33x_watchdog "ERROR: $watchdog_device did not appear"
	return 0 2>/dev/null || exit 0
fi

(
	if ! exec 3>"$watchdog_device"; then
		log_a33x_watchdog "ERROR: failed to open $watchdog_device"
		exit 1
	fi

	log_a33x_watchdog "started early feeder on $watchdog_device (8 second interval)"

	while printf 'K' >&3; do
		sleep 8
	done

	log_a33x_watchdog "ERROR: watchdog write failed"
) &

a33x_watchdog_pid=$!
mkdir -p /run
printf '%s\n' "$a33x_watchdog_pid" > /run/a33x-watchdog.pid
log_a33x_watchdog "feeder pid=$a33x_watchdog_pid"

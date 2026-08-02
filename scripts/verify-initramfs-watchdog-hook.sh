#!/usr/bin/env bash
set -euo pipefail

INITRAMFS="${1:-$HOME/a33-port/export-debug/initramfs}"
EXPECTED_ENTRY="hooks/01-a33x-watchdog.sh"

if [[ ! -f "$INITRAMFS" ]]; then
    echo "Initramfs not found: $INITRAMFS" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/root"
(
    cd "$TMP/root"
    gzip -dc "$INITRAMFS" | cpio -idmu --no-absolute-filenames >/dev/null 2>&1
)

HOOK="$TMP/root/$EXPECTED_ENTRY"

if [[ ! -f "$HOOK" ]]; then
    echo "REFUSING IMAGE: missing $EXPECTED_ENTRY" >&2
    exit 1
fi

if [[ ! -x "$HOOK" ]]; then
    echo "REFUSING IMAGE: $EXPECTED_ENTRY is not executable" >&2
    exit 1
fi

sh -n "$HOOK"

for required in \
    'a33x-watchdog-v2:' \
    '/sys/class/watchdog/watchdog0/dev' \
    'mknod /dev/watchdog0 c "$major" "$minor"' \
    'watchdog_device=/dev/watchdog0' \
    '/dev/watchdog' \
    'mknod /dev/kmsg c 1 11' \
    'exec 3>"$watchdog_device"' \
    "printf 'K' >&3" \
    'sleep 8'
do
    if ! grep -Fq "$required" "$HOOK"; then
        echo "REFUSING IMAGE: watchdog hook lacks required marker: $required" >&2
        exit 1
    fi
done

echo "Watchdog hook v2 present: $EXPECTED_ENTRY"
echo "Primary watchdog: /dev/watchdog0 (resolved from sysfs)"
echo "Legacy fallback: /dev/watchdog"
echo "Kernel logging: /dev/kmsg created when absent"
echo "Ping interval: 8 seconds"

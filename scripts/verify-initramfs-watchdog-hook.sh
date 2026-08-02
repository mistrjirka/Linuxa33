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

for required in \
    '/dev/watchdog' \
    "exec 3>\"\$watchdog_device\"" \
    "printf 'K' >&3" \
    'sleep 8'
do
    if ! grep -Fq "$required" "$HOOK"; then
        echo "REFUSING IMAGE: watchdog hook lacks required marker: $required" >&2
        exit 1
    fi
done

echo "Watchdog hook present: $EXPECTED_ENTRY"
echo "Watchdog device: /dev/watchdog"
echo "Ping interval: 8 seconds"

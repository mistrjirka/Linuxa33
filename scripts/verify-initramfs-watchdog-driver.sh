#!/usr/bin/env bash
set -euo pipefail

INITRAMFS="${1:-$HOME/a33-port/export-debug/initramfs}"
EXPECTED_MODULE_RE='(^|/)s3c2410_wdt\.ko(\.(gz|xz|zst))?$'

if [[ ! -f "$INITRAMFS" ]]; then
    echo "Initramfs not found: $INITRAMFS" >&2
    exit 1
fi

entries="$(gzip -dc "$INITRAMFS" | cpio -it 2>/dev/null)"

module_entry="$(
    printf '%s\n' "$entries" |
        grep -E "$EXPECTED_MODULE_RE" |
        head -n1 || true
)"

if [[ -z "$module_entry" ]]; then
    echo "REFUSING IMAGE: s3c2410_wdt.ko is missing from the initramfs" >&2
    echo "The A33 kernel builds CONFIG_S3C2410_WATCHDOG=m, so neither the" >&2
    echo "kernel watchdog handover nor a userspace /dev/watchdog0 feeder can" >&2
    echo "work until this driver is present and loaded." >&2
    exit 1
fi

hook_entry="$(
    printf '%s\n' "$entries" |
        grep -E '(^|/)hooks/01-a33x-watchdog\.sh$' |
        head -n1 || true
)"

echo "Watchdog driver present: $module_entry"
if [[ -n "$hook_entry" ]]; then
    echo "Userspace watchdog hook: present ($hook_entry)"
else
    echo "Userspace watchdog hook: absent"
fi

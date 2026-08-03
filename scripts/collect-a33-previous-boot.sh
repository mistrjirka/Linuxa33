#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

LABEL="${1:-candidate}"
PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
EXPECTED_TWRP_SHA256="${EXPECTED_TWRP_SHA256:-414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$RESULT_ROOT/${LABEL}-result-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"

for command in "$ADB" grep tar sha256sum awk date mkdir cp; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

mkdir -p "$OUT"

echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "TWRP ADB is ready"

capture_required() {
    local remote_command="$1"
    local output="$2"
    if ! "$ADB" shell "$remote_command" > "$output"; then
        echo "Required capture failed: $remote_command" >&2
        exit 1
    fi
    if [[ ! -s "$output" ]]; then
        echo "Required capture is empty: $output" >&2
        exit 1
    fi
}

capture_optional() {
    local remote_command="$1"
    local output="$2"
    "$ADB" shell "$remote_command" > "$output" 2>&1 || true
}

echo "=== Capture previous Linux boot ==="
capture_required 'cat /proc/last_kmsg' "$OUT/last_kmsg.txt"

echo "=== Capture current TWRP state ==="
capture_required 'dmesg' "$OUT/twrp-dmesg.txt"
capture_optional 'getprop' "$OUT/twrp-getprop.txt"
capture_optional 'cat /proc/cmdline' "$OUT/twrp-cmdline.txt"
capture_optional 'ls -la /proc/last_kmsg /sys/fs/pstore 2>&1; find /sys/fs/pstore -maxdepth 1 -type f -print 2>/dev/null' "$OUT/log-source-state.txt"
capture_optional 'uname -a; cat /proc/version' "$OUT/twrp-kernel.txt"
capture_optional 'ls -l /dev/block/by-name/recovery /dev/block/sda16 2>&1' "$OUT/recovery-block-state.txt"
capture_required 'sha256sum /dev/block/by-name/recovery' "$OUT/recovery-sha256.txt"

RECOVERY_SHA256="$(awk 'NR == 1 {print $1}' "$OUT/recovery-sha256.txt")"
if [[ "$RECOVERY_SHA256" == "$EXPECTED_TWRP_SHA256" ]]; then
    RECOVERY_STATUS="verified-known-good-twrp"
else
    RECOVERY_STATUS="unexpected-recovery-hash"
fi

PATTERN='a33x-muic-switch|s2mu106|usbpd|pdic|muic|i2c|dwc3|gadget|watchdog|USB_ATTACH_UFP|reserve_state|runtime_resume|runtime_suspend|conndone|connect.done|reset|panic|Call trace|BUG:|Oops|Unable to handle'
grep -Ein "$PATTERN" "$OUT/last_kmsg.txt" > "$OUT/relevant-last-kmsg.txt" || true
grep -Ein "$PATTERN" "$OUT/twrp-dmesg.txt" > "$OUT/relevant-twrp-dmesg.txt" || true

count_pattern() {
    local pattern="$1"
    local file="$2"
    grep -Eic "$pattern" "$file" 2>/dev/null || true
}

{
    echo "label=$LABEL"
    echo "created=$(date -Ins)"
    echo "out=$OUT"
    echo "last_kmsg_bytes=$(stat -Lc '%s' "$OUT/last_kmsg.txt")"
    echo "recovery_sha256=$RECOVERY_SHA256"
    echo "recovery_status=$RECOVERY_STATUS"
    echo "muic_helper_begin_count=$(count_pattern 'a33x-muic-switch-v1: begin' "$OUT/last_kmsg.txt")"
    echo "muic_helper_success_count=$(count_pattern 'a33x-muic-switch-v1: success' "$OUT/last_kmsg.txt")"
    echo "muic_helper_error_count=$(count_pattern 'a33x-muic-switch-v1:.*(error|failed|refus)' "$OUT/last_kmsg.txt")"
    echo "typec_ufp_count=$(count_pattern 'USB_ATTACH_UFP' "$OUT/last_kmsg.txt")"
    echo "dwc3_gadget_start_count=$(count_pattern '__dwc3_gadget_start|Turn on gadget|dwc3_gadget_run_stop' "$OUT/last_kmsg.txt")"
    echo "dwc3_reset_count=$(count_pattern 'dwc3_gadget_reset_interrupt' "$OUT/last_kmsg.txt")"
    echo "dwc3_conndone_count=$(count_pattern 'dwc3_gadget_conndone_interrupt|connect.done' "$OUT/last_kmsg.txt")"
    echo "kernel_panic_count=$(count_pattern 'Kernel panic|panic - not syncing' "$OUT/last_kmsg.txt")"
} | tee "$OUT/summary.txt"

for source in \
    "$PORT_ROOT/build/u0e-muic-switch-helper.txt" \
    "$PORT_ROOT/build/u0e-third-host-prepare.txt" \
    "$PORT_ROOT/build/u0e-third-host-recovery-build.txt" \
    "$PORT_ROOT/build/u0e-host-kernel-live.txt" \
    "$PORT_ROOT/build/u0e-host-lsusb-live.txt" \
    "$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0e-muic-switch-manifest.txt"
do
    if [[ -f "$source" ]]; then
        cp -a "$source" "$OUT/"
    fi
done

tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "Previous-boot result collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Summary:"
cat "$OUT/summary.txt"

if [[ "$RECOVERY_STATUS" != "verified-known-good-twrp" ]]; then
    echo "WARNING: recovery partition does not match the known-good TWRP hash" >&2
fi

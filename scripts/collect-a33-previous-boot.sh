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

for command in "$ADB" grep tar sha256sum awk date mkdir cp python3; do
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

echo "=== Sanitize Samsung binary/wrapped last_kmsg ==="
python3 - "$OUT/last_kmsg.txt" "$OUT/last_kmsg.sanitized.txt" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_bytes()
output = []
for byte in source:
    if byte in (9, 10, 13) or 32 <= byte < 127:
        output.append(chr(byte))
    elif byte == 0:
        output.append("\n")
    else:
        output.append("\ufffd")

Path(sys.argv[2]).write_text("".join(output), encoding="utf-8")
PY

PATTERN='a33x-(watchdog|usbpd|muic-switch)|s2mu106|usbpd|pdic|muic|i2c|dwc3|gadget|USB_ATTACH_UFP|reserve_state|runtime_resume|runtime_suspend|conndone|connect.done|reset|Kernel panic|panic - not syncing|Call trace|BUG:|Oops|Unable to handle'
grep -aEin "$PATTERN" "$OUT/last_kmsg.sanitized.txt" > "$OUT/relevant-last-kmsg.txt" || true
grep -aEin "$PATTERN" "$OUT/twrp-dmesg.txt" > "$OUT/relevant-twrp-dmesg.txt" || true

count_pattern() {
    local pattern="$1"
    local file="$2"
    grep -aEic "$pattern" "$file" 2>/dev/null || true
}

SANITIZED="$OUT/last_kmsg.sanitized.txt"
CUSTOM_KMSG_COUNT="$(count_pattern 'a33x-(watchdog|usbpd|muic-switch)' "$SANITIZED")"
I2C_DEV_INIT_COUNT="$(count_pattern 'i2c /dev entries driver' "$SANITIZED")"

if (( I2C_DEV_INIT_COUNT > 0 )); then
    U0E_HOOK_REACHED_I2C_DEV="yes"
else
    U0E_HOOK_REACHED_I2C_DEV="no"
fi

if (( CUSTOM_KMSG_COUNT == 0 && I2C_DEV_INIT_COUNT > 0 )); then
    USERSPACE_KMSG_RELIABILITY="not-preserved-or-overwritten"
else
    USERSPACE_KMSG_RELIABILITY="present-or-inconclusive"
fi

{
    echo "label=$LABEL"
    echo "created=$(date -Ins)"
    echo "out=$OUT"
    echo "last_kmsg_bytes=$(stat -Lc '%s' "$OUT/last_kmsg.txt")"
    echo "recovery_sha256=$RECOVERY_SHA256"
    echo "recovery_status=$RECOVERY_STATUS"
    echo "last_kmsg_custom_userspace_marker_count=$CUSTOM_KMSG_COUNT"
    echo "last_kmsg_userspace_kmsg_reliability=$USERSPACE_KMSG_RELIABILITY"
    echo "i2c_dev_kernel_init_count=$I2C_DEV_INIT_COUNT"
    echo "u0e_hook_reached_i2c_dev=$U0E_HOOK_REACHED_I2C_DEV"
    echo "muic_helper_begin_count=$(count_pattern 'a33x-muic-switch-v1: begin' "$SANITIZED")"
    echo "muic_helper_success_count=$(count_pattern 'a33x-muic-switch-v1: success' "$SANITIZED")"
    echo "muic_helper_error_count=$(count_pattern 'a33x-muic-switch-v1:.*(error|failed|refus)' "$SANITIZED")"
    echo "typec_ufp_count=$(count_pattern 'USB_ATTACH_UFP' "$SANITIZED")"
    echo "reserve_replay_count=$(count_pattern 'reserve_state_check event=vbus\(1\) enable=1' "$SANITIZED")"
    echo "dwc3_gadget_start_count=$(count_pattern '__dwc3_gadget_start|Turn on gadget|dwc3_gadget_run_stop' "$SANITIZED")"
    echo "dwc3_reset_count=$(count_pattern 'dwc3_gadget_reset_interrupt' "$SANITIZED")"
    echo "dwc3_conndone_count=$(count_pattern 'dwc3_gadget_conndone_interrupt|connect.done' "$SANITIZED")"
    echo "kernel_panic_count=$(count_pattern 'Kernel panic|panic - not syncing' "$SANITIZED")"
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

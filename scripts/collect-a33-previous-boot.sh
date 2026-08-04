#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

LABEL="${1:-candidate}"
PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"
EXPECTED_TWRP_SHA256="${EXPECTED_TWRP_SHA256:-414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
METADATA_DEVICE="${METADATA_DEVICE:-/dev/block/by-name/metadata}"
METADATA_MOUNTPOINT="${METADATA_MOUNTPOINT:-/tmp/a33x-metadata-collect}"
METADATA_RESULT_RELATIVE="${METADATA_RESULT_RELATIVE:-a33x-bringup/u0f-muic-result.txt}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$RESULT_ROOT/${LABEL}-result-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"

for command in "$ADB" grep tar sha256sum awk date mkdir cp python3 stat sleep; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

mkdir -p "$OUT"

echo "=== Wait for TWRP ADB shell ==="
a33_init_recovery_adb 30

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

echo "=== Capture persistent U0f metadata result when present ==="
METADATA_CAPTURE="$OUT/u0f-metadata-result.txt"
if "$ADB" shell sh -s -- \
    "$METADATA_DEVICE" "$METADATA_MOUNTPOINT" "$METADATA_RESULT_RELATIVE" \
    > "$METADATA_CAPTURE" <<'SH'
set -eu

DEVICE="$1"
MOUNTPOINT="$2"
RELATIVE="$3"

if [ ! -b "$DEVICE" ]; then
    echo "metadata_result_status=block-device-missing"
    exit 3
fi

resolved="$(readlink -f "$DEVICE" 2>/dev/null || true)"
existing_mount="$(awk -v a="$DEVICE" -v b="$resolved" '$1==a || $1==b {print $2; exit}' /proc/mounts 2>/dev/null || true)"
mounted_here=no

if [ -n "$existing_mount" ]; then
    root="$existing_mount"
else
    mkdir -p "$MOUNTPOINT"
    umount "$MOUNTPOINT" 2>/dev/null || true
    if ! mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$DEVICE" "$MOUNTPOINT"; then
        echo "metadata_result_status=readonly-mount-failed"
        echo "metadata_device=$DEVICE"
        echo "metadata_resolved=${resolved:-unknown}"
        exit 4
    fi
    root="$MOUNTPOINT"
    mounted_here=yes
fi

cleanup() {
    if [ "$mounted_here" = yes ]; then
        umount "$MOUNTPOINT" 2>/dev/null || true
    fi
}
trap cleanup EXIT

target="$root/$RELATIVE"
echo "metadata_device=$DEVICE"
echo "metadata_resolved=${resolved:-unknown}"
echo "metadata_mount_root=$root"
echo "metadata_result_relative=/$RELATIVE"

if [ ! -f "$target" ]; then
    echo "metadata_result_status=missing"
    exit 5
fi

if [ ! -s "$target" ]; then
    echo "metadata_result_status=empty"
    exit 6
fi

echo "metadata_result_status=present"
echo "metadata_result_bytes=$(wc -c < "$target")"
echo "metadata_result_sha256=$(sha256sum "$target" | awk '{print $1}')"
echo "metadata_result_begin"
cat "$target"
echo "metadata_result_end"
SH
then
    metadata_rc=0
else
    metadata_rc=$?
    case "$LABEL" in
        u0f*)
            echo "Required U0f metadata result capture failed (rc=$metadata_rc)" >&2
            cat "$METADATA_CAPTURE" >&2 || true
            exit "$metadata_rc"
            ;;
        *)
            echo "Persistent U0f result unavailable for label=$LABEL (rc=$metadata_rc)"
            ;;
    esac
fi

if [[ ! -s "$METADATA_CAPTURE" ]]; then
    echo "metadata_result_status=not-captured" > "$METADATA_CAPTURE"
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

PATTERN='a33x-(watchdog|usbpd|muic-switch|muic-persist)|s2mu106|usbpd|pdic|muic|i2c|dwc3|gadget|USB_ATTACH_UFP|reserve_state|runtime_resume|runtime_suspend|conndone|connect.done|reset|Kernel panic|panic - not syncing|Call trace|BUG:|Oops|Unable to handle'
grep -aEin "$PATTERN" "$OUT/last_kmsg.sanitized.txt" > "$OUT/relevant-last-kmsg.txt" || true
grep -aEin "$PATTERN" "$OUT/twrp-dmesg.txt" > "$OUT/relevant-twrp-dmesg.txt" || true

count_pattern() {
    local pattern="$1"
    local file="$2"
    grep -aEic "$pattern" "$file" 2>/dev/null || true
}

value_from_file() {
    local key="$1"
    local file="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key) + 2); exit}' "$file" 2>/dev/null || true
}

SANITIZED="$OUT/last_kmsg.sanitized.txt"
CUSTOM_KMSG_COUNT="$(count_pattern 'a33x-(watchdog|usbpd|muic-switch|muic-persist)' "$SANITIZED")"
I2C_DEV_INIT_COUNT="$(count_pattern 'i2c /dev entries driver' "$SANITIZED")"
METADATA_RESULT_STATUS="$(value_from_file metadata_result_status "$METADATA_CAPTURE")"
PERSISTED_HELPER_PRESENT="$(value_from_file helper_output_present "$METADATA_CAPTURE")"
PERSISTED_HELPER_SUCCESS="$(value_from_file helper_success_marker "$METADATA_CAPTURE")"
PERSISTED_HELPER_ERROR="$(value_from_file helper_error_or_rollback_marker "$METADATA_CAPTURE")"
PERSISTED_I2C_LOADED="$(value_from_file i2c_dev_loaded "$METADATA_CAPTURE")"
PERSISTED_I2C_TARGET="$(value_from_file i2c2_adapter_target "$METADATA_CAPTURE")"
PERSISTED_I2C_NODE="$(value_from_file i2c2_character_device "$METADATA_CAPTURE")"
PERSISTED_ADDRESS_OWNED="$(value_from_file address_2_003e_owned "$METADATA_CAPTURE")"

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
    echo "metadata_result_status=${METADATA_RESULT_STATUS:-unknown}"
    echo "persisted_i2c_dev_loaded=${PERSISTED_I2C_LOADED:-unknown}"
    echo "persisted_i2c2_adapter_target=${PERSISTED_I2C_TARGET:-unknown}"
    echo "persisted_i2c2_character_device=${PERSISTED_I2C_NODE:-unknown}"
    echo "persisted_address_2_003e_owned=${PERSISTED_ADDRESS_OWNED:-unknown}"
    echo "persisted_helper_output_present=${PERSISTED_HELPER_PRESENT:-unknown}"
    echo "persisted_helper_success_marker=${PERSISTED_HELPER_SUCCESS:-unknown}"
    echo "persisted_helper_error_or_rollback_marker=${PERSISTED_HELPER_ERROR:-unknown}"
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
    "$PORT_ROOT/build/u0f-muic-persist.txt" \
    "$PORT_ROOT/build/u0e-third-host-prepare.txt" \
    "$PORT_ROOT/build/u0e-third-host-recovery-build.txt" \
    "$PORT_ROOT/build/u0f-third-host-prepare.txt" \
    "$PORT_ROOT/build/u0f-third-host-recovery-build.txt" \
    "$PORT_ROOT/build/u0e-host-kernel-live.txt" \
    "$PORT_ROOT/build/u0e-host-lsusb-live.txt" \
    "$PORT_ROOT/build/u0f-host-kernel-live.txt" \
    "$PORT_ROOT/build/u0f-host-lsusb-live.txt" \
    "$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0e-muic-switch-manifest.txt" \
    "$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0f-muic-persist-manifest.txt"
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

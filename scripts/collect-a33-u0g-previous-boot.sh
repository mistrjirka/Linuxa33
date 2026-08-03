#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
RESULT_ROOT="${RESULT_ROOT:-$PORT_ROOT/build/runtime-results}"
METADATA_DEVICE="${METADATA_DEVICE:-/dev/block/by-name/metadata}"
METADATA_MOUNTPOINT="${METADATA_MOUNTPOINT:-/tmp/a33x-metadata-u0g-precheck}"
METADATA_RESULT_RELATIVE="a33x-bringup/u0g-muic-result.txt"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_COLLECTOR="$SCRIPT_DIR/collect-a33-previous-boot.sh"

if [[ ! -x "$BASE_COLLECTOR" && ! -f "$BASE_COLLECTOR" ]]; then
    echo "Missing required collector: $BASE_COLLECTOR" >&2
    exit 1
fi

until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Require persistent U0g result before general collection ==="
"$ADB" shell sh -s -- \
    "$METADATA_DEVICE" "$METADATA_MOUNTPOINT" "$METADATA_RESULT_RELATIVE" <<'SH'
set -eu
DEVICE="$1"
MOUNTPOINT="$2"
RELATIVE="$3"

if [ ! -b "$DEVICE" ]; then
    echo "REFUSING: metadata block device missing: $DEVICE" >&2
    exit 1
fi

resolved="$(readlink -f "$DEVICE" 2>/dev/null || true)"
existing_mount="$(awk -v a="$DEVICE" -v b="$resolved" '$1==a || $1==b {print $2; exit}' /proc/mounts 2>/dev/null || true)"
mounted_here=no
if [ -n "$existing_mount" ]; then
    root="$existing_mount"
else
    mkdir -p "$MOUNTPOINT"
    umount "$MOUNTPOINT" 2>/dev/null || true
    mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$DEVICE" "$MOUNTPOINT"
    root="$MOUNTPOINT"
    mounted_here=yes
fi

cleanup() {
    [ "$mounted_here" = no ] || umount "$MOUNTPOINT" 2>/dev/null || true
}
trap cleanup EXIT

target="$root/$RELATIVE"
if [ ! -s "$target" ]; then
    echo "REFUSING: persistent U0g result is missing or empty: /$RELATIVE" >&2
    exit 1
fi

echo "u0g_metadata_result_precheck=passed"
echo "u0g_metadata_result_bytes=$(wc -c < "$target")"
echo "u0g_metadata_result_sha256=$(sha256sum "$target" | awk '{print $1}')"
SH

METADATA_RESULT_RELATIVE="$METADATA_RESULT_RELATIVE" \
    bash "$BASE_COLLECTOR" u0g

OUT="$(find "$RESULT_ROOT" -maxdepth 1 -type d -name 'u0g-result-*' -printf '%T@ %p\n' \
    | sort -nr | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}')"
if [[ -z "$OUT" || ! -d "$OUT" ]]; then
    echo "REFUSING: could not locate newly collected U0g result directory" >&2
    exit 1
fi

for source in \
    "$PORT_ROOT/build/u0g-muic-dynamic.txt" \
    "$PORT_ROOT/build/u0g-third-host-prepare.txt" \
    "$PORT_ROOT/build/u0g-third-host-recovery-build.txt" \
    "$PORT_ROOT/build/u0g-host-kernel-live.txt" \
    "$PORT_ROOT/build/u0g-host-lsusb-live.txt" \
    "$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-manifest.txt"
do
    if [[ -f "$source" ]]; then
        cp -a "$source" "$OUT/"
    fi
done

# The generic collector retains its historic U0f filename even when it captures
# the U0g-relative metadata path. Preserve it for compatibility and add the
# correctly named U0g copy.
BASE_METADATA_CAPTURE="$OUT/u0f-metadata-result.txt"
U0G_METADATA_CAPTURE="$OUT/u0g-metadata-result.txt"
if [[ ! -s "$BASE_METADATA_CAPTURE" ]]; then
    echo "REFUSING: base collector did not capture the U0g metadata result" >&2
    exit 1
fi
cp -a "$BASE_METADATA_CAPTURE" "$U0G_METADATA_CAPTURE"

capture_value() {
    local key="$1"
    awk -F= -v key="$key" \
        '$1==key {print substr($0, length(key) + 2); exit}' \
        "$U0G_METADATA_CAPTURE" 2>/dev/null || true
}

SELECTED_BUS="$(capture_value selected_bus)"
SELECTED_TARGET="$(capture_value selected_target)"
SELECTED_DEVICE="$(capture_value selected_device)"
HELPER_RC="$(capture_value helper_rc)"
HELPER_OUTPUT_PRESENT="$(capture_value helper_output_present)"
HELPER_SUCCESS="$(capture_value helper_success_marker)"
HELPER_ERROR="$(capture_value helper_error_or_rollback_marker)"
WRAPPED_PANIC_MATCHES="$(awk -F= \
    '$1=="kernel_panic_count" {print $2; exit}' \
    "$OUT/summary.txt" 2>/dev/null || true)"

HOST_KERNEL_LOG="$OUT/u0g-host-kernel-live.txt"
HOST_ENUMERATION=no
HOST_NCM=no
HOST_ACM=no
if [[ -f "$HOST_KERNEL_LOG" ]]; then
    grep -Fq 'Product: Samsung Galaxy A33 5G' "$HOST_KERNEL_LOG" \
        && HOST_ENUMERATION=yes || true
    grep -Fq 'cdc_ncm' "$HOST_KERNEL_LOG" \
        && HOST_NCM=yes || true
    grep -Fq 'ttyACM0: USB ACM device' "$HOST_KERNEL_LOG" \
        && HOST_ACM=yes || true
fi

INITIAL_CTRL1="$(grep -aoE \
    'initial .*ctrl1=0x[0-9a-fA-F]+' "$U0G_METADATA_CAPTURE" \
    | head -n 1 \
    | sed -n 's/.*ctrl1=\(0x[0-9a-fA-F]*\).*/\1/p')"
INITIAL_SWITCH="$(grep -aoE \
    'initial .*switch=0x[0-9a-fA-F]+' "$U0G_METADATA_CAPTURE" \
    | head -n 1 \
    | sed -n 's/.*switch=\(0x[0-9a-fA-F]*\).*/\1/p')"
FINAL_CTRL1="$(grep -aoE \
    'success .*ctrl1=0x[0-9a-fA-F]+' "$U0G_METADATA_CAPTURE" \
    | head -n 1 \
    | sed -n 's/.*ctrl1=\(0x[0-9a-fA-F]*\).*/\1/p')"
FINAL_SWITCH="$(grep -aoE \
    'success .*switch=0x[0-9a-fA-F]+' "$U0G_METADATA_CAPTURE" \
    | head -n 1 \
    | sed -n 's/.*switch=\(0x[0-9a-fA-F]*\).*/\1/p')"

if [[ "$HELPER_SUCCESS" == yes \
    && "$HELPER_RC" == 0 \
    && "$HOST_ENUMERATION" == yes ]]
then
    RESULT_CLASSIFICATION="muic-switch-success-and-host-usb-enumeration"
else
    RESULT_CLASSIFICATION="requires-manual-review"
fi

{
    echo "label=u0g"
    echo "result_classification=$RESULT_CLASSIFICATION"
    echo "selected_controller=13860000.hsi2c"
    echo "selected_bus=${SELECTED_BUS:-unknown}"
    echo "selected_target=${SELECTED_TARGET:-unknown}"
    echo "selected_device=${SELECTED_DEVICE:-unknown}"
    echo "helper_rc=${HELPER_RC:-unknown}"
    echo "helper_output_present=${HELPER_OUTPUT_PRESENT:-unknown}"
    echo "helper_success_marker=${HELPER_SUCCESS:-unknown}"
    echo "helper_error_or_rollback_marker=${HELPER_ERROR:-unknown}"
    echo "initial_ctrl1=${INITIAL_CTRL1:-unknown}"
    echo "initial_switch=${INITIAL_SWITCH:-unknown}"
    echo "final_ctrl1=${FINAL_CTRL1:-unknown}"
    echo "final_switch=${FINAL_SWITCH:-unknown}"
    echo "host_usb_enumeration=$HOST_ENUMERATION"
    echo "host_cdc_ncm=$HOST_NCM"
    echo "host_cdc_acm=$HOST_ACM"
    echo "wrapped_last_kmsg_panic_matches=${WRAPPED_PANIC_MATCHES:-unknown}"
    echo "wrapped_last_kmsg_panic_attribution=unreliable-mixed-buffer"
    echo "current_u0g_boot_panic_evidence=none"
    echo "current_u0g_boot_panic_reason=metadata-report-at-uptime-3.61-and-host-enumeration-contradict-panic-at-1.45"
} | tee "$OUT/u0g-summary.txt"

ARCHIVE="$OUT.tar.gz"
tar -C "$RESULT_ROOT" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "U0g previous-boot result collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Persistent result: $U0G_METADATA_CAPTURE"
echo "U0g summary:      $OUT/u0g-summary.txt"

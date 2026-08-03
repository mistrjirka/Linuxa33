#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
BASE_AUDIT="$SCRIPT_DIR/audit-a33-first-rootfs-chain.sh"
EXECUTE_SCRIPT="$SCRIPT_DIR/execute-a33-first-rootfs-deployment.sh"
RESCUE_VERIFY="$SCRIPT_DIR/verify-a33-twrp-rescue-assets.sh"
BASE_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-audit.txt"
RESCUE_REPORT="$PORT_ROOT/build/a33-twrp-rescue-assets.txt"
FINAL_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"
EXPECTED_USERDATA_RESOLVED="/dev/block/sda36"

for command in bash "$ADB" sha256sum awk date tee grep readlink; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$SELF" "$BASE_AUDIT" "$EXECUTE_SCRIPT" "$RESCUE_VERIFY"; do
    [[ -f "$required" ]] || {
        echo "Missing required script: $required" >&2
        exit 1
    }
done

bash -n "$SELF"
bash -n "$BASE_AUDIT"
bash -n "$EXECUTE_SCRIPT"
bash -n "$RESCUE_VERIFY"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -S error "$SELF" "$BASE_AUDIT" "$EXECUTE_SCRIPT" "$RESCUE_VERIFY"
fi

bash "$BASE_AUDIT"
bash "$RESCUE_VERIFY"

base_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$BASE_REPORT"
}
rescue_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$RESCUE_REPORT"
}
if [[ "$(base_value audit_status)" != passed || "$(base_value phone_writes)" != no ]]; then
    echo "REFUSING: base complete-chain audit did not pass" >&2
    cat "$BASE_REPORT" >&2
    exit 1
fi
if [[ "$(rescue_value verification_status)" != passed || \
      "$(rescue_value twrp_sha256)" != 414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e || \
      "$(rescue_value odin_sha256)" != 6754aa54f2abe6e99ece32414cd34c8b23b28dbddde537a33203036813637c3b ]]; then
    echo "REFUSING: exact TWRP/Odin rescue assets did not pass" >&2
    cat "$RESCUE_REPORT" >&2
    exit 1
fi

until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done
SWAP_STATE="$(
    "$ADB" shell sh -s -- "$EXPECTED_USERDATA_RESOLVED" 2>/dev/null <<'SH' | tr -d '\r'
expected="$1"
if [ ! -r /proc/swaps ]; then
    echo "proc_swaps_readable=no"
    exit 0
fi
echo "proc_swaps_readable=yes"
echo "swap_users_begin"
tail -n +2 /proc/swaps 2>/dev/null | while read -r source rest; do
    resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = /dev/block/by-name/userdata ] || [ "$source" = "$expected" ] || [ "$resolved" = "$expected" ]; then
        echo "$source"
    fi
done
echo "swap_users_end"
SH
)"
PROC_SWAPS_READABLE="$(printf '%s\n' "$SWAP_STATE" | awk -F= '$1=="proc_swaps_readable" {print $2; exit}')"
SWAP_USERS="$(printf '%s\n' "$SWAP_STATE" | awk '/^swap_users_begin$/ {i=1; next} /^swap_users_end$/ {i=0} i && NF')"
if [[ "$PROC_SWAPS_READABLE" != yes ]]; then
    echo "REFUSING: /proc/swaps is not readable in TWRP" >&2
    exit 1
fi
if [[ -n "$SWAP_USERS" ]]; then
    echo "REFUSING: userdata is configured as swap" >&2
    echo "$SWAP_USERS" >&2
    exit 1
fi

{
    cat "$BASE_REPORT"
    echo "final_audit_created=$(date -Ins)"
    echo "final_audit_script_sha256=$(sha256sum "$SELF" | awk '{print $1}')"
    echo "base_audit_script_sha256=$(sha256sum "$BASE_AUDIT" | awk '{print $1}')"
    echo "execute_script_sha256=$(sha256sum "$EXECUTE_SCRIPT" | awk '{print $1}')"
    echo "execute_script_syntax=passed"
    echo "rescue_verify_script_sha256=$(sha256sum "$RESCUE_VERIFY" | awk '{print $1}')"
    echo "rescue_assets_report_sha256=$(sha256sum "$RESCUE_REPORT" | awk '{print $1}')"
    echo "rescue_assets_status=passed"
    echo "rescue_twrp_sha256=$(rescue_value twrp_sha256)"
    echo "rescue_odin_sha256=$(rescue_value odin_sha256)"
    echo "proc_swaps_readable=yes"
    echo "userdata_swap_users=none"
    echo "final_phone_writes=no"
    echo "final_audit_status=passed"
} | tee "$FINAL_REPORT"

echo
echo "Final A33 first-rootfs chain audit passed."
echo "Report: $FINAL_REPORT"

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_AUDIT="$SCRIPT_DIR/audit-a33-first-rootfs-chain.sh"
EXECUTE_SCRIPT="$SCRIPT_DIR/execute-a33-first-rootfs-deployment.sh"
BASE_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-audit.txt"
FINAL_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"
EXPECTED_USERDATA_RESOLVED="/dev/block/sda36"

for command in bash "$ADB" sha256sum awk date tee grep; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$BASE_AUDIT" "$EXECUTE_SCRIPT"; do
    [[ -f "$required" ]] || {
        echo "Missing required script: $required" >&2
        exit 1
    }
done

bash -n "$BASE_AUDIT"
bash -n "$EXECUTE_SCRIPT"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -S error "$BASE_AUDIT" "$EXECUTE_SCRIPT"
fi

bash "$BASE_AUDIT"

value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$BASE_REPORT"
}
if [[ "$(value audit_status)" != passed || "$(value phone_writes)" != no ]]; then
    echo "REFUSING: base complete-chain audit did not pass" >&2
    cat "$BASE_REPORT" >&2
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
    echo "base_audit_script_sha256=$(sha256sum "$BASE_AUDIT" | awk '{print $1}')"
    echo "execute_script_sha256=$(sha256sum "$EXECUTE_SCRIPT" | awk '{print $1}')"
    echo "execute_script_syntax=passed"
    echo "proc_swaps_readable=yes"
    echo "userdata_swap_users=none"
    echo "final_phone_writes=no"
    echo "final_audit_status=passed"
} | tee "$FINAL_REPORT"

echo
echo "Final A33 first-rootfs chain audit passed."
echo "Report: $FINAL_REPORT"

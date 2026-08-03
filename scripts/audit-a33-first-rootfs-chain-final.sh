#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_AUDIT="$SCRIPT_DIR/audit-a33-first-rootfs-chain.sh"
EXECUTE_SCRIPT="$SCRIPT_DIR/execute-a33-first-rootfs-deployment.sh"
BASE_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-audit.txt"
FINAL_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"

for command in bash sha256sum awk date tee; do
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

{
    cat "$BASE_REPORT"
    echo "final_audit_created=$(date -Ins)"
    echo "base_audit_script_sha256=$(sha256sum "$BASE_AUDIT" | awk '{print $1}')"
    echo "execute_script_sha256=$(sha256sum "$EXECUTE_SCRIPT" | awk '{print $1}')"
    echo "execute_script_syntax=passed"
    echo "final_phone_writes=no"
    echo "final_audit_status=passed"
} | tee "$FINAL_REPORT"

echo
echo "Final A33 first-rootfs chain audit passed."
echo "Report: $FINAL_REPORT"

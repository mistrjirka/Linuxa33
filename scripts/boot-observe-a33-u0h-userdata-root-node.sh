#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0h-userdata-root-node-manifest.txt}"
FLASH_REPORT="${FLASH_REPORT:-$PORT_ROOT/build/a33-first-rootfs-u0h-flash.txt}"
BASE_OBSERVER="$SCRIPT_DIR/boot-observe-a33-first-rootfs.sh"

for command in bash awk; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$MANIFEST" "$FLASH_REPORT" "$BASE_OBSERVER"; do
    [[ -f "$required" ]] || {
        echo "Missing required U0h observation input: $required" >&2
        exit 1
    }
done
value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}
EXPECTED_SHA="$(value "$MANIFEST" recovery_sha256)"
if [[ "$(value "$MANIFEST" candidate)" != U0h-userdata-root-node || \
      "$(value "$MANIFEST" build_status)" != passed || \
      "$(value "$FLASH_REPORT" flash_status)" != passed || \
      "$(value "$FLASH_REPORT" recovery_partition_sha256)" != "$EXPECTED_SHA" ]]; then
    echo "REFUSING: U0h candidate or flash report is not valid" >&2
    exit 1
fi

env \
    FLASH_REPORT="$FLASH_REPORT" \
    EXPECTED_RECOVERY_SHA256="$EXPECTED_SHA" \
    MAX_SECONDS="${MAX_SECONDS:-180}" \
    bash "$BASE_OBSERVER"

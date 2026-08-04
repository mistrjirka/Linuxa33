#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0h-userdata-root-node-manifest.txt}"
FLASH_REPORT="${FLASH_REPORT:-$PORT_ROOT/build/a33-first-rootfs-u0h-flash.txt}"
BASE_OBSERVER="$SCRIPT_DIR/boot-observe-a33-first-rootfs.sh"

for command in bash awk python3 mktemp cp ln rm; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$MANIFEST" "$FLASH_REPORT" "$BASE_OBSERVER" \
    "$SCRIPT_DIR/lib/a33-adb-runtime.sh"; do
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

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
cp "$BASE_OBSERVER" "$TMP/observe.sh"
ln -s "$SCRIPT_DIR/lib" "$TMP/lib"
python3 - "$TMP/observe.sh" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
old = 'EXPECTED_RECOVERY_SHA256="e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"'
new = 'EXPECTED_RECOVERY_SHA256="${EXPECTED_RECOVERY_SHA256:-e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81}"'
if text.count(old) != 1:
    raise SystemExit("base observer recovery-hash contract changed")
path.write_text(text.replace(old, new))
PY
bash -n "$TMP/observe.sh"

env \
    FLASH_REPORT="$FLASH_REPORT" \
    EXPECTED_RECOVERY_SHA256="$EXPECTED_SHA" \
    MAX_SECONDS="${MAX_SECONDS:-180}" \
    bash "$TMP/observe.sh"

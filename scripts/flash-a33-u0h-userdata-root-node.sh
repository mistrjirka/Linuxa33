#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFEST="${MANIFEST:-$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0h-userdata-root-node-manifest.txt}"
BASE_FLASH="$SCRIPT_DIR/flash-a33-u0g-after-userdata-deploy.sh"
REPORT="$PORT_ROOT/build/a33-first-rootfs-u0h-flash.txt"

for command in bash awk sha256sum stat python3 mktemp cp ln rm; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$MANIFEST" "$BASE_FLASH" "$SCRIPT_DIR/lib/a33-adb-runtime.sh"; do
    [[ -f "$required" ]] || {
        echo "Missing required U0h flash input: $required" >&2
        exit 1
    }
done
value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$MANIFEST"
}

CANDIDATE="$(value recovery)"
EXPECTED_SHA="$(value recovery_sha256)"
EXPECTED_SIZE="$(value recovery_size)"
if [[ "$(value candidate)" != U0h-userdata-root-node || \
      "$(value preparation_status)" != passed || \
      "$(value build_status)" != passed || \
      ! "$EXPECTED_SHA" =~ ^[0-9a-f]{64}$ || \
      "$EXPECTED_SIZE" != 100663296 || \
      ! -f "$CANDIDATE" ]]; then
    echo "REFUSING: U0h manifest did not pass" >&2
    cat "$MANIFEST" >&2
    exit 1
fi
[[ "$(stat -Lc '%s' "$CANDIDATE")" = "$EXPECTED_SIZE" ]] || {
    echo "REFUSING: U0h candidate size changed" >&2
    exit 1
}
[[ "$(sha256sum "$CANDIDATE" | awk '{print $1}')" = "$EXPECTED_SHA" ]] || {
    echo "REFUSING: U0h candidate SHA256 changed" >&2
    exit 1
}

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
cp "$BASE_FLASH" "$TMP/flash.sh"
ln -s "$SCRIPT_DIR/lib" "$TMP/lib"
python3 - "$TMP/flash.sh" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text()
replacements = {
    'REPORT="$PORT_ROOT/build/a33-first-rootfs-u0g-flash.txt"':
        'REPORT="${REPORT:-$PORT_ROOT/build/a33-first-rootfs-u0g-flash.txt}"',
    'EXPECTED_CANDIDATE_SHA256="e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81"':
        'EXPECTED_CANDIDATE_SHA256="${EXPECTED_CANDIDATE_SHA256:-e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81}"',
    'EXPECTED_CANDIDATE_SIZE=100663296':
        'EXPECTED_CANDIDATE_SIZE="${EXPECTED_CANDIDATE_SIZE:-100663296}"',
}
for old, new in replacements.items():
    if text.count(old) != 1:
        raise SystemExit(f"base flash contract changed: {old}")
    text = text.replace(old, new)
path.write_text(text)
PY
bash -n "$TMP/flash.sh"

env \
    CANDIDATE="$CANDIDATE" \
    EXPECTED_CANDIDATE_SHA256="$EXPECTED_SHA" \
    EXPECTED_CANDIDATE_SIZE="$EXPECTED_SIZE" \
    REPORT="$REPORT" \
    bash "$TMP/flash.sh"

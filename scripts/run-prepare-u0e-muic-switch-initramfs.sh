#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/prepare-u0e-muic-switch-initramfs.sh"
RUNTIME="$SCRIPT_DIR/.prepare-u0e-muic-switch-initramfs.runtime.$$"

if [[ ! -f "$SOURCE" ]]; then
    echo "Missing U0e preparation script: $SOURCE" >&2
    exit 1
fi

cleanup() {
    rm -f "$RUNTIME"
}
trap cleanup EXIT

python3 - "$SOURCE" "$RUNTIME" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
runtime = Path(sys.argv[2])
text = source.read_text()
old = 'pmbootstrap chroot -r -- apk add --upgrade "$PACKAGE"'
new = 'pmbootstrap chroot -r --add "$PACKAGE" -- true'
count = text.count(old)
if count != 1:
    raise SystemExit(
        f"REFUSING: expected exactly one raw local APK install command, found {count}"
    )
runtime.write_text(text.replace(old, new))
runtime.chmod(0o755)
PY

bash "$RUNTIME" "$@"

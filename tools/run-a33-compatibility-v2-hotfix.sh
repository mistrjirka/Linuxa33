#!/usr/bin/env bash
set -Eeuo pipefail

TOOLS_DIR="${A33_TOOLS_DIR:-$HOME/Stažené}"
PATCHER="$TOOLS_DIR/apply-a33-compatibility-fixes-v1-direct.py"
AUDIT="$TOOLS_DIR/audit-a33-command-compatibility-v2-direct.sh"
RUNNER="$TOOLS_DIR/run-a33-compatibility-fixes-v1-direct.sh"
REPO_ROOT="${1:-$HOME/Linuxa33}"

OLD_PATCHER_SHA=d83ecbb56b45bd53a7402aea87548f6585716567dbf3d526779ae3f11a460663
FIXED_PATCHER_SHA=54b1e8c6600116cd38d7a39d75fde78c704320510c52561256c01042f430c650
AUDIT_SHA=101d748917068d8a0d531a01e64f6c50c36381e6ebba0c8f6ec2067ae2eeb707
RUNNER_SHA=c97e14965e866ff88e2599b487036459afcae34938e93e5e2c741c9ab0a777f6

for command in python3 sha256sum bash; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$PATCHER" "$AUDIT" "$RUNNER"; do
    [[ -f "$required" ]] || {
        echo "Missing downloaded compatibility file: $required" >&2
        exit 1
    }
done

[[ "$(sha256sum "$AUDIT" | awk '{print $1}')" = "$AUDIT_SHA" ]] || {
    echo "Audit file identity mismatch: $AUDIT" >&2
    exit 1
}
[[ "$(sha256sum "$RUNNER" | awk '{print $1}')" = "$RUNNER_SHA" ]] || {
    echo "Runner file identity mismatch: $RUNNER" >&2
    exit 1
}

CURRENT_SHA="$(sha256sum "$PATCHER" | awk '{print $1}')"
case "$CURRENT_SHA" in
    "$FIXED_PATCHER_SHA")
        ;;
    "$OLD_PATCHER_SHA")
        python3 - "$PATCHER" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

replacements = [
    ("        '''READBACK_SHA=\"$(\n", "        r'''READBACK_SHA=\"$(\n"),
    ("        '''WRITTEN_PREFIX_SHA=\"$(\n", "        r'''WRITTEN_PREFIX_SHA=\"$(\n"),
    (
        "        '''if [[ -z \"$OUT\" || ! -d \"$OUT\" ]]; then\n"
        "    echo \"REFUSING: base first-rootfs result directory was not found\" >&2\n"
        "    exit 1\n"
        "fi\n\n"
        "ROOT_IDENTITY=",
        "        r'''if [[ -z \"$OUT\" || ! -d \"$OUT\" ]]; then\n"
        "    echo \"REFUSING: base first-rootfs result directory was not found\" >&2\n"
        "    exit 1\n"
        "fi\n\n"
        "ROOT_IDENTITY=",
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one patcher literal, found {count}: {old[:48]!r}")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
PY
        ;;
    *)
        echo "Unexpected patcher identity: $CURRENT_SHA" >&2
        exit 1
        ;;
esac

[[ "$(sha256sum "$PATCHER" | awk '{print $1}')" = "$FIXED_PATCHER_SHA" ]] || {
    echo "Patcher hotfix SHA256 verification failed" >&2
    exit 1
}
python3 -m py_compile "$PATCHER"

exec bash "$RUNNER" "$REPO_ROOT"

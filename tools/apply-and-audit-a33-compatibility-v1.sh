#!/usr/bin/env bash
set -Eeuo pipefail

REPO_ROOT="${1:-$HOME/Linuxa33}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
TOOL_DIR="$(cd "$(dirname "$SELF")" && pwd)"
PAYLOAD_DIR="$TOOL_DIR/payloads"
START_HEAD="$(git -C "$REPO_ROOT" rev-parse HEAD)"
TMP="$(mktemp -d)"
DONE=0

cleanup() {
    local rc=$?
    rm -rf "$TMP"
    if ((rc != 0 && DONE == 0)); then
        git -C "$REPO_ROOT" restore -- scripts docs >/dev/null 2>&1 || true
        rm -f "$REPO_ROOT/scripts/lib/a33-adb-runtime.sh"
        echo "All tracked compatibility changes were rolled back." >&2
    fi
    trap - EXIT INT TERM HUP
    exit "$rc"
}
trap cleanup EXIT INT TERM HUP

for command in python3 base64 gzip sha256sum bash git mktemp rm readlink; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required host command: $command" >&2
        exit 1
    }
done

[[ "$(git -C "$REPO_ROOT" rev-parse HEAD)" = "$START_HEAD" ]] || exit 1
[[ -f "$PAYLOAD_DIR/apply-a33-compatibility-fixes-v1.py.gz.b64" ]] || exit 1
[[ -f "$PAYLOAD_DIR/audit-a33-command-compatibility-v2-fixed.sh.gz.b64" ]] || exit 1

base64 -d "$PAYLOAD_DIR/apply-a33-compatibility-fixes-v1.py.gz.b64" |
    gzip -d > "$TMP/apply.py"
base64 -d "$PAYLOAD_DIR/audit-a33-command-compatibility-v2-fixed.sh.gz.b64" |
    gzip -d > "$TMP/audit.sh"

printf '%s  %s\n' \
    b1398c03e1d31fb41876b32d5be5dd446de6089a4d61a7f4598daf91cf0e2a75 \
    "$TMP/apply.py" |
    sha256sum -c -
printf '%s  %s\n' \
    165bd14fa6ab3ad652855293d015ce9a96ad34f306af1bbc99bcc1599fdf61da \
    "$TMP/audit.sh" |
    sha256sum -c -

python3 -m py_compile "$TMP/apply.py"
bash -n "$TMP/audit.sh"
python3 "$TMP/apply.py" "$REPO_ROOT"
REPO_ROOT="$REPO_ROOT" bash "$TMP/audit.sh"

DONE=1
trap - EXIT INT TERM HUP
rm -rf "$TMP"

echo
echo "Compatibility patch and non-destructive audit passed."
echo "No persistent phone partition was written."
echo "Patched scripts remain uncommitted in: $REPO_ROOT"

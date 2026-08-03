#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ODIN="${ODIN:-$PORT_ROOT/tools/odin4}"
RESCUE_TAR="${RESCUE_TAR:-$PORT_ROOT/build/rescue/twrp-a33x-restore.img.tar}"
REPORT="$PORT_ROOT/build/a33-twrp-rescue-assets.txt"
EXPECTED_ODIN_SHA256="6754aa54f2abe6e99ece32414cd34c8b23b28dbddde537a33203036813637c3b"
EXPECTED_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_TWRP_SIZE=100663296

for command in sha256sum stat tar find awk grep mktemp date mkdir; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

for required in "$ODIN" "$RESCUE_TAR"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: rescue asset is missing: $required" >&2
        exit 1
    }
done
[[ -x "$ODIN" ]] || {
    echo "REFUSING: Odin binary is not executable: $ODIN" >&2
    exit 1
}

ODIN_SHA="$(sha256sum "$ODIN" | awk '{print $1}')"
if [[ "$ODIN_SHA" != "$EXPECTED_ODIN_SHA256" ]]; then
    echo "REFUSING: Odin SHA256 mismatch" >&2
    echo "expected=$EXPECTED_ODIN_SHA256 actual=$ODIN_SHA" >&2
    exit 1
fi

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

tar -tf "$RESCUE_TAR" > "$TMP/entries.txt"
mapfile -t ENTRIES < <(grep -Ev '^[[:space:]]*$' "$TMP/entries.txt")
if [[ "${#ENTRIES[@]}" -ne 1 || "${ENTRIES[0]#./}" != recovery.img ]]; then
    echo "REFUSING: rescue tar must contain exactly recovery.img" >&2
    cat "$TMP/entries.txt" >&2
    exit 1
fi

tar -xf "$RESCUE_TAR" -C "$TMP"
TWRP="$TMP/recovery.img"
TWRP_SIZE="$(stat -Lc '%s' "$TWRP")"
TWRP_SHA="$(sha256sum "$TWRP" | awk '{print $1}')"
if [[ "$TWRP_SIZE" != "$EXPECTED_TWRP_SIZE" || "$TWRP_SHA" != "$EXPECTED_TWRP_SHA256" ]]; then
    echo "REFUSING: rescue TWRP identity mismatch" >&2
    echo "expected_size=$EXPECTED_TWRP_SIZE actual_size=$TWRP_SIZE" >&2
    echo "expected_sha=$EXPECTED_TWRP_SHA256 actual_sha=$TWRP_SHA" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"
{
    echo "created=$(date -Ins)"
    echo "operation=verify-exact-twrp-rescue-assets"
    echo "odin=$ODIN"
    echo "odin_sha256=$ODIN_SHA"
    echo "rescue_tar=$RESCUE_TAR"
    echo "rescue_tar_sha256=$(sha256sum "$RESCUE_TAR" | awk '{print $1}')"
    echo "tar_entries=1"
    echo "twrp_size=$TWRP_SIZE"
    echo "twrp_sha256=$TWRP_SHA"
    echo "verification_status=passed"
} | tee "$REPORT"

echo
echo "Exact TWRP rescue assets verified."
echo "Report: $REPORT"

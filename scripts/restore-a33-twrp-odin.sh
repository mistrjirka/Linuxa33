#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

CONFIRMATION="${1:-}"
REQUIRED_CONFIRMATION="RESTORE-EXACT-TWRP"
PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ODIN="${ODIN:-$PORT_ROOT/tools/odin4}"
RESCUE_TAR="${RESCUE_TAR:-$PORT_ROOT/build/rescue/twrp-a33x-restore.img.tar}"
VERIFY_SCRIPT="${VERIFY_SCRIPT:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/verify-a33-twrp-rescue-assets.sh}"
VERIFY_REPORT="$PORT_ROOT/build/a33-twrp-rescue-assets.txt"
REPORT="$PORT_ROOT/build/a33-twrp-odin-restore.txt"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: this command flashes the recovery partition through Samsung Download Mode.

Put the phone in Download Mode first, then run with the exact token:

  bash $0 $REQUIRED_CONFIRMATION

This restores only the exact known-good TWRP recovery image. It does not write
userdata, cache, super, Android boot, or the GPT.
EOF
    exit 2
fi

for command in sudo awk sha256sum date mkdir tee; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
[[ -f "$VERIFY_SCRIPT" ]] || {
    echo "Missing rescue verifier: $VERIFY_SCRIPT" >&2
    exit 1
}

bash "$VERIFY_SCRIPT"
value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$VERIFY_REPORT"
}
if [[ "$(value verification_status)" != passed || \
      "$(value odin)" != "$ODIN" || \
      "$(value rescue_tar)" != "$RESCUE_TAR" ]]; then
    echo "REFUSING: rescue asset report did not pass the exact contract" >&2
    cat "$VERIFY_REPORT" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"
LIST_LOG="$PORT_ROOT/build/a33-odin-list.txt"
FLASH_LOG="$PORT_ROOT/build/a33-odin-restore-output.txt"

echo "=== Confirm Download Mode device is visible to Odin ==="
sudo "$ODIN" -l 2>&1 | tee "$LIST_LOG"

echo "=== Restore exact known-good TWRP recovery ==="
sudo "$ODIN" -a "$RESCUE_TAR" 2>&1 | tee "$FLASH_LOG"

{
    echo "created=$(date -Ins)"
    echo "operation=restore-exact-twrp-through-odin"
    echo "odin=$ODIN"
    echo "odin_sha256=$(value odin_sha256)"
    echo "rescue_tar=$RESCUE_TAR"
    echo "rescue_tar_sha256=$(value rescue_tar_sha256)"
    echo "twrp_sha256=$(value twrp_sha256)"
    echo "userdata_written=no"
    echo "cache_written=no"
    echo "super_written=no"
    echo "boot_written=no"
    echo "recovery_written=yes"
    echo "odin_command_status=passed"
    echo "next_action=boot-twrp-directly-before-android"
} | tee "$REPORT"

echo
echo "Odin restore command completed."
echo "Immediately boot TWRP directly; do not boot Android first."
echo "Report: $REPORT"

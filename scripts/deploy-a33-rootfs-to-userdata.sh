#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
HANDOFF_REPORT="${HANDOFF_REPORT:-$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt}"
IMPLEMENTATION_COMMIT="fa581b166700ea3243beac67a4c0e8aba1a7255e"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for command in git awk mktemp chmod; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

if [[ ! -f "$HANDOFF_REPORT" ]]; then
    echo "REFUSING: exact U0g unified-root handoff report is missing" >&2
    echo "Run scripts/verify-a33-u0g-unified-root-handoff.sh first." >&2
    exit 1
fi

report_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$HANDOFF_REPORT"
}

if [[ "$(report_value verification_status)" != passed || \
      "$(report_value cache_partition_required)" != no || \
      "$(report_value pmos_boot_required_before_second_stage)" != no || \
      "$(report_value recovery_sha256)" != e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81 || \
      "$(report_value ramdisk_sha256)" != 13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d ]]; then
    echo "REFUSING: U0g unified-root handoff report did not pass the exact contract" >&2
    cat "$HANDOFF_REPORT" >&2
    exit 1
fi

if ! git -C "$REPO_ROOT" cat-file -e "$IMPLEMENTATION_COMMIT^{commit}" 2>/dev/null; then
    echo "REFUSING: reviewed userdata deployment implementation commit is unavailable" >&2
    echo "missing_commit=$IMPLEMENTATION_COMMIT" >&2
    exit 1
fi

TMP="$(mktemp)"
cleanup() { rm -f "$TMP"; }
trap cleanup EXIT

git -C "$REPO_ROOT" show \
    "$IMPLEMENTATION_COMMIT:scripts/deploy-a33-rootfs-to-userdata.sh" \
    > "$TMP"
chmod 700 "$TMP"

# The implementation commit is immutable and already contains all destructive
# gates: exact confirmation token, private preflight match, exact TWRP hash,
# userdata mapping/size checks, unmounted and unused target checks, full written
# prefix SHA256 readback, filesystem identity checks and a read-only mount test.
exec bash "$TMP" "$@"

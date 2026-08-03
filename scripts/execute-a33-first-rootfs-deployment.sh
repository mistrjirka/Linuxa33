#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

CONFIRMATION="${1:-}"
REQUIRED_CONFIRMATION="ERASE-ANDROID-USERDATA-INSTALL-PMOS"
PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT_REPORT="${AUDIT_REPORT:-$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt}"
DEPLOY="$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: final destructive wrapper requires the exact token:

  bash $0 $REQUIRED_CONFIRMATION

Run scripts/audit-a33-first-rootfs-chain-final.sh immediately before this command.
EOF
    exit 2
fi

for command in sha256sum awk readlink stat; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

[[ -f "$AUDIT_REPORT" && -f "$DEPLOY" ]] || {
    echo "REFUSING: final chain-audit report or deploy implementation is missing" >&2
    exit 1
}

value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$AUDIT_REPORT"
}

if [[ "$(value audit_status)" != passed || \
      "$(value final_audit_status)" != passed || \
      "$(value phone_writes)" != no || \
      "$(value final_phone_writes)" != no || \
      "$(value bash_syntax_all)" != passed || \
      "$(value execute_script_syntax)" != passed || \
      "$(value obsolete_cache_scripts)" != refusing-stubs || \
      "$(value u0g_handoff_status)" != passed || \
      "$(value private_backup_checksums)" != passed || \
      "$(value userdata_unmounted)" != yes || \
      "$(value userdata_device_mapper_users)" != none || \
      "$(value proc_swaps_readable)" != yes || \
      "$(value userdata_swap_users)" != none ]]; then
    echo "REFUSING: final complete-chain audit report did not pass" >&2
    cat "$AUDIT_REPORT" >&2
    exit 1
fi

check_script() {
    local key="$1" path="$2" expected actual
    expected="$(value "$key")"
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING: audited script changed: $path" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
}

check_script execute_script_sha256 "$SELF"
check_script deploy_script_sha256 "$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh"
check_script flash_script_sha256 "$SCRIPT_DIR/flash-a33-u0g-after-userdata-deploy.sh"
check_script observe_script_sha256 "$SCRIPT_DIR/boot-observe-a33-first-rootfs.sh"
check_script live_collector_sha256 "$SCRIPT_DIR/collect-a33-first-rootfs-live.sh"
check_script failure_collector_sha256 "$SCRIPT_DIR/collect-a33-first-rootfs-previous-boot.sh"
check_script restore_script_sha256 "$SCRIPT_DIR/restore-a33-twrp-odin.sh"

HANDOFF="$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt"
IMAGE="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img" 2>/dev/null || true)"
IMAGE_MANIFEST="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt" 2>/dev/null || true)"
PREFLIGHT_DIR="$(value private_backup_dir)"
PREFLIGHT_MANIFEST="$PREFLIGHT_DIR/manifest.txt"
PREFLIGHT_SUMS="$PREFLIGHT_DIR/SHA256SUMS"

for required in "$HANDOFF" "$IMAGE" "$IMAGE_MANIFEST" "$PREFLIGHT_MANIFEST" "$PREFLIGHT_SUMS"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: audited input is missing: $required" >&2
        exit 1
    }
done

compare_hash() {
    local key="$1" path="$2" expected actual
    expected="$(value "$key")"
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "REFUSING: audited input changed: $path" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
}

compare_hash u0g_handoff_report_sha256 "$HANDOFF"
compare_hash userdata_image_sha256 "$IMAGE"
compare_hash userdata_image_manifest_sha256 "$IMAGE_MANIFEST"
compare_hash private_backup_manifest_sha256 "$PREFLIGHT_MANIFEST"
compare_hash private_backup_sha256sums_sha256 "$PREFLIGHT_SUMS"

if [[ "$(stat -Lc '%s' "$IMAGE")" != "$(value userdata_image_size)" ]]; then
    echo "REFUSING: audited userdata image size changed" >&2
    exit 1
fi

exec bash "$DEPLOY" "$REQUIRED_CONFIRMATION"

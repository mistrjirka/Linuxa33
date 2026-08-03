#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

CONFIRMATION="${1:-}"
REQUIRED_CONFIRMATION="ERASE-ANDROID-USERDATA-INSTALL-PMOS"
PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
AUDIT_SCRIPT="$SCRIPT_DIR/audit-a33-first-rootfs-transport-final.sh"
AUDIT_REPORT="${AUDIT_REPORT:-$PORT_ROOT/build/a33-first-rootfs-transport-final-audit.txt}"
FINAL_CHAIN_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"
STAGE_REPORT="$PORT_ROOT/build/a33-userdata-rootfs-stage.txt"
DEPLOY="$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: final destructive wrapper requires the exact token:

  bash $0 $REQUIRED_CONFIRMATION

Run scripts/audit-a33-first-rootfs-transport-final.sh immediately before this
command. That audit stages and fully reads back the image using the actual ADB
transport available on this host.
EOF
    exit 2
fi

for command in sha256sum awk readlink stat; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

for required in \
    "$SELF" \
    "$AUDIT_SCRIPT" \
    "$AUDIT_REPORT" \
    "$FINAL_CHAIN_REPORT" \
    "$STAGE_REPORT" \
    "$DEPLOY"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: required transport-bound deployment file is missing: $required" >&2
        exit 1
    }
done

value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$AUDIT_REPORT"
}

if [[ "$(value transport_final_audit_status)" != passed || \
      "$(value final_chain_audit_status)" != passed || \
      "$(value staging_status)" != passed || \
      "$(value adb_exec_in_required)" != no || \
      "$(value adb_push_full_image)" != passed || \
      "$(value adb_exec_out_full_readback)" != passed || \
      "$(value python_socket_support)" != passed || \
      "$(value bash_dev_tcp_support)" != passed || \
      "$(value ssh_strict_host_key_accept_new)" != passed || \
      "$(value private_backup_checksums)" != passed || \
      "$(value rescue_assets_status)" != passed || \
      "$(value persistent_phone_partition_writes)" != no ]]; then
    echo "REFUSING: real-transport final audit did not pass" >&2
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

check_script transport_audit_script_sha256 "$AUDIT_SCRIPT"
check_script stage_script_sha256 "$SCRIPT_DIR/stage-a33-userdata-rootfs-in-twrp.sh"
check_script execute_script_sha256 "$SELF"
check_script deploy_script_sha256 "$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh"
check_script flash_script_sha256 "$SCRIPT_DIR/flash-a33-u0g-after-userdata-deploy.sh"
check_script observe_script_sha256 "$SCRIPT_DIR/boot-observe-a33-first-rootfs.sh"
check_script live_collector_sha256 "$SCRIPT_DIR/collect-a33-first-rootfs-live.sh"
check_script failure_collector_sha256 "$SCRIPT_DIR/collect-a33-first-rootfs-previous-boot.sh"
check_script restore_script_sha256 "$SCRIPT_DIR/restore-a33-twrp-odin.sh"

compare_hash() {
    local key="$1" path="$2" expected actual
    expected="$(value "$key")"
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING: audited report or image changed: $path" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
}

compare_hash final_chain_audit_report_sha256 "$FINAL_CHAIN_REPORT"
compare_hash stage_report_sha256 "$STAGE_REPORT"

IMAGE="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img" 2>/dev/null || true)"
[[ -f "$IMAGE" ]] || {
    echo "REFUSING: audited userdata image is missing" >&2
    exit 1
}
compare_hash userdata_image_sha256 "$IMAGE"
if [[ "$(stat -Lc '%s' "$IMAGE")" != "$(value userdata_image_size)" ]]; then
    echo "REFUSING: audited userdata image size changed" >&2
    exit 1
fi

exec bash "$DEPLOY" "$REQUIRED_CONFIRMATION"
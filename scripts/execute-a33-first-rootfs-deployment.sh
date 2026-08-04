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
AUDIT_SCRIPT="$SCRIPT_DIR/audit-a33-first-rootfs-transport-bound-final.sh"
AUDIT_REPORT="${AUDIT_REPORT:-$PORT_ROOT/build/a33-first-rootfs-transport-bound-final-audit.txt}"
TRANSPORT_REPORT="$PORT_ROOT/build/a33-first-rootfs-transport-final-audit.txt"
COMMAND_AUDIT="$SCRIPT_DIR/audit-a33-command-capabilities.sh"
COMMAND_REPORT="$PORT_ROOT/build/a33-command-capabilities.txt"
FINAL_CHAIN_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"
STAGE_REPORT="$PORT_ROOT/build/a33-userdata-rootfs-stage.txt"
DEPLOY="$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh"

if [[ "$CONFIRMATION" != "$REQUIRED_CONFIRMATION" ]]; then
    cat >&2 <<EOF
REFUSING: final destructive wrapper requires the exact token:

  bash $0 $REQUIRED_CONFIRMATION

Run scripts/audit-a33-first-rootfs-transport-bound-final.sh immediately before
this command. It proves and binds the actual allowed host, ADB, TWRP and rootfs
commands, the full ADB push/exec-out transport, and every rescue artifact.
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
    "$SELF" "$AUDIT_SCRIPT" "$AUDIT_REPORT" "$TRANSPORT_REPORT" \
    "$COMMAND_AUDIT" "$COMMAND_REPORT" "$FINAL_CHAIN_REPORT" \
    "$STAGE_REPORT" "$DEPLOY"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: required bound deployment file is missing: $required" >&2
        exit 1
    }
done

value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$AUDIT_REPORT"
}

if [[ "$(value transport_bound_final_audit_status)" != passed || \
      "$(value transport_final_audit_status)" != passed || \
      "$(value command_capability_audit_status)" != passed || \
      "$(value final_chain_audit_status)" != passed || \
      "$(value staging_status)" != passed || \
      "$(value all_command_capability_bindings)" != passed || \
      "$(value all_underlying_artifact_bindings)" != passed || \
      "$(value adb_exec_in_required)" != no || \
      "$(value adb_push_full_image)" != passed || \
      "$(value adb_exec_out_full_readback)" != passed || \
      "$(value python_socket_support)" != passed || \
      "$(value python_tcp_probe)" != passed || \
      "$(value ssh_strict_host_key_accept_new)" != passed || \
      "$(value private_backup_checksums)" != passed || \
      "$(value rescue_assets_status)" != passed || \
      "$(value persistent_phone_partition_writes)" != no ]]; then
    echo "REFUSING: bound command and transport audit did not pass" >&2
    cat "$AUDIT_REPORT" >&2
    exit 1
fi

compare_hash() {
    local key="$1" path="$2" expected actual
    expected="$(value "$key")"
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING: audited script, report, image, or backup changed: $path" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
}

compare_hash bound_audit_script_sha256 "$AUDIT_SCRIPT"
compare_hash transport_audit_report_sha256 "$TRANSPORT_REPORT"
compare_hash command_capability_report_sha256 "$COMMAND_REPORT"
compare_hash command_audit_script_sha256 "$COMMAND_AUDIT"
compare_hash final_chain_audit_report_sha256 "$FINAL_CHAIN_REPORT"
compare_hash stage_report_sha256 "$STAGE_REPORT"

SCRIPTS=(
    lib/a33-adb-runtime.sh
    audit-a33-command-capabilities.sh
    stage-a33-userdata-rootfs-in-twrp.sh
    deploy-a33-rootfs-to-userdata.sh
    execute-a33-first-rootfs-deployment.sh
    flash-a33-u0g-after-userdata-deploy.sh
    boot-observe-a33-first-rootfs.sh
    collect-a33-first-rootfs-live.sh
    collect-a33-first-rootfs-previous-boot.sh
    restore-a33-twrp-odin.sh
)
KEYS=(
    adb_runtime_helper_sha256
    command_audit_script_sha256
    stage_script_sha256
    deploy_script_sha256
    execute_script_sha256
    flash_script_sha256
    observe_script_sha256
    live_collector_sha256
    failure_collector_sha256
    restore_script_sha256
)
for index in "${!SCRIPTS[@]}"; do
    compare_hash "${KEYS[$index]}" "$SCRIPT_DIR/${SCRIPTS[$index]}"
done

HANDOFF="$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt"
IMAGE="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img" 2>/dev/null || true)"
IMAGE_MANIFEST="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt" 2>/dev/null || true)"
PREFLIGHT_DIR="$(value private_backup_dir)"
PREFLIGHT_MANIFEST="$PREFLIGHT_DIR/manifest.txt"
PREFLIGHT_SUMS="$PREFLIGHT_DIR/SHA256SUMS"
RESCUE_REPORT="$PORT_ROOT/build/a33-twrp-rescue-assets.txt"

for required in \
    "$HANDOFF" "$IMAGE" "$IMAGE_MANIFEST" \
    "$PREFLIGHT_MANIFEST" "$PREFLIGHT_SUMS" "$RESCUE_REPORT"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: bound artifact is missing: $required" >&2
        exit 1
    }
done

compare_hash handoff_report_sha256 "$HANDOFF"
compare_hash userdata_image_sha256 "$IMAGE"
compare_hash userdata_image_manifest_sha256 "$IMAGE_MANIFEST"
compare_hash private_backup_manifest_sha256 "$PREFLIGHT_MANIFEST"
compare_hash private_backup_sha256sums_sha256 "$PREFLIGHT_SUMS"
compare_hash rescue_assets_report_sha256 "$RESCUE_REPORT"

if [[ "$(stat -Lc '%s' "$IMAGE")" != "$(value userdata_image_size)" ]]; then
    echo "REFUSING: audited userdata image size changed" >&2
    exit 1
fi

exec bash "$DEPLOY" "$REQUIRED_CONFIRMATION"
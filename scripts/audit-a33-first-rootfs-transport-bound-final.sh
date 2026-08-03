#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
TRANSPORT_AUDIT="$SCRIPT_DIR/audit-a33-first-rootfs-transport-final.sh"
TRANSPORT_REPORT="$PORT_ROOT/build/a33-first-rootfs-transport-final-audit.txt"
COMMAND_AUDIT="$SCRIPT_DIR/audit-a33-command-capabilities.sh"
COMMAND_REPORT="$PORT_ROOT/build/a33-command-capabilities.txt"
FINAL_CHAIN_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"
STAGE_REPORT="$PORT_ROOT/build/a33-userdata-rootfs-stage.txt"
REPORT="$PORT_ROOT/build/a33-first-rootfs-transport-bound-final-audit.txt"

for command in bash sha256sum awk readlink stat date tee; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$SELF" "$TRANSPORT_AUDIT" "$COMMAND_AUDIT"; do
    [[ -f "$required" ]] || {
        echo "Missing required script: $required" >&2
        exit 1
    }
done

bash -n "$SELF"
bash -n "$TRANSPORT_AUDIT"
bash -n "$COMMAND_AUDIT"
if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -S error "$SELF" "$TRANSPORT_AUDIT" "$COMMAND_AUDIT"
fi

bash "$TRANSPORT_AUDIT"

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

for required in \
    "$TRANSPORT_REPORT" "$COMMAND_REPORT" "$FINAL_CHAIN_REPORT" "$STAGE_REPORT"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: required audit report is missing: $required" >&2
        exit 1
    }
done

if [[ "$(value "$TRANSPORT_REPORT" transport_final_audit_status)" != passed || \
      "$(value "$TRANSPORT_REPORT" command_capability_audit_status)" != passed || \
      "$(value "$TRANSPORT_REPORT" adb_exec_in_required)" != no || \
      "$(value "$TRANSPORT_REPORT" adb_push_full_image)" != passed || \
      "$(value "$TRANSPORT_REPORT" adb_exec_out_full_readback)" != passed || \
      "$(value "$TRANSPORT_REPORT" private_backup_checksums)" != passed || \
      "$(value "$TRANSPORT_REPORT" rescue_assets_status)" != passed || \
      "$(value "$TRANSPORT_REPORT" persistent_phone_partition_writes)" != no || \
      "$(value "$COMMAND_REPORT" command_capability_audit_status)" != passed || \
      "$(value "$COMMAND_REPORT" adb_exec_in_used)" != no || \
      "$(value "$COMMAND_REPORT" adb_help_feature_detection_used)" != no || \
      "$(value "$COMMAND_REPORT" twrp_required_commands_status)" != passed || \
      "$(value "$FINAL_CHAIN_REPORT" final_audit_status)" != passed || \
      "$(value "$STAGE_REPORT" staging_status)" != passed ]]; then
    echo "REFUSING: transport, command, final-chain, or staging report did not pass" >&2
    exit 1
fi

compare_hash() {
    local expected="$1" path="$2" actual
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING: audited artifact changed: $path" >&2
        echo "expected=$expected actual=$actual" >&2
        exit 1
    fi
}

compare_hash "$(value "$TRANSPORT_REPORT" final_chain_audit_report_sha256)" "$FINAL_CHAIN_REPORT"
compare_hash "$(value "$TRANSPORT_REPORT" command_capability_report_sha256)" "$COMMAND_REPORT"
compare_hash "$(value "$TRANSPORT_REPORT" stage_report_sha256)" "$STAGE_REPORT"
compare_hash "$(value "$TRANSPORT_REPORT" transport_audit_script_sha256)" "$TRANSPORT_AUDIT"
compare_hash "$(value "$TRANSPORT_REPORT" command_audit_script_sha256)" "$COMMAND_AUDIT"

HANDOFF="$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt"
IMAGE="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img" 2>/dev/null || true)"
IMAGE_MANIFEST="$(readlink -f "$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt" 2>/dev/null || true)"
PREFLIGHT_DIR="$(value "$FINAL_CHAIN_REPORT" private_backup_dir)"
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

compare_hash "$(value "$FINAL_CHAIN_REPORT" u0g_handoff_report_sha256)" "$HANDOFF"
compare_hash "$(value "$TRANSPORT_REPORT" userdata_image_sha256)" "$IMAGE"
compare_hash "$(value "$FINAL_CHAIN_REPORT" userdata_image_manifest_sha256)" "$IMAGE_MANIFEST"
compare_hash "$(value "$FINAL_CHAIN_REPORT" private_backup_manifest_sha256)" "$PREFLIGHT_MANIFEST"
compare_hash "$(value "$FINAL_CHAIN_REPORT" private_backup_sha256sums_sha256)" "$PREFLIGHT_SUMS"
compare_hash "$(value "$FINAL_CHAIN_REPORT" rescue_assets_report_sha256)" "$RESCUE_REPORT"

if [[ "$(stat -Lc '%s' "$IMAGE")" != "$(value "$TRANSPORT_REPORT" userdata_image_size)" || \
      "$(value "$STAGE_REPORT" source_sha256)" != "$(value "$TRANSPORT_REPORT" userdata_image_sha256)" || \
      "$(value "$STAGE_REPORT" remote_sha256)" != "$(value "$TRANSPORT_REPORT" userdata_image_sha256)" || \
      "$(value "$STAGE_REPORT" full_readback_sha256)" != "$(value "$TRANSPORT_REPORT" userdata_image_sha256)" ]]; then
    echo "REFUSING: image size or staged/readback identity changed" >&2
    exit 1
fi

SCRIPTS=(
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
    path="$SCRIPT_DIR/${SCRIPTS[$index]}"
    bash -n "$path"
    compare_hash "$(value "$TRANSPORT_REPORT" "${KEYS[$index]}")" "$path"
done

{
    cat "$TRANSPORT_REPORT"
    echo "bound_audit_created=$(date -Ins)"
    echo "bound_audit_script_sha256=$(sha256sum "$SELF" | awk '{print $1}')"
    echo "transport_audit_report_sha256=$(sha256sum "$TRANSPORT_REPORT" | awk '{print $1}')"
    echo "command_capability_report_sha256=$(sha256sum "$COMMAND_REPORT" | awk '{print $1}')"
    echo "command_audit_script_sha256=$(sha256sum "$COMMAND_AUDIT" | awk '{print $1}')"
    echo "handoff_report_sha256=$(sha256sum "$HANDOFF" | awk '{print $1}')"
    echo "userdata_image_manifest_sha256=$(sha256sum "$IMAGE_MANIFEST" | awk '{print $1}')"
    echo "private_backup_dir=$PREFLIGHT_DIR"
    echo "private_backup_manifest_sha256=$(sha256sum "$PREFLIGHT_MANIFEST" | awk '{print $1}')"
    echo "private_backup_sha256sums_sha256=$(sha256sum "$PREFLIGHT_SUMS" | awk '{print $1}')"
    echo "rescue_assets_report_sha256=$(sha256sum "$RESCUE_REPORT" | awk '{print $1}')"
    echo "all_command_capability_bindings=passed"
    echo "all_underlying_artifact_bindings=passed"
    echo "persistent_phone_partition_writes=no"
    echo "transport_bound_final_audit_status=passed"
} | tee "$REPORT"

echo
echo "A33 transport and command audit is bound to every underlying artifact."
echo "Report: $REPORT"
echo "The verified rootfs remains staged in volatile TWRP /tmp."
#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMMAND_AUDIT="$SCRIPT_DIR/audit-a33-command-capabilities.sh"
COMMAND_REPORT="$PORT_ROOT/build/a33-command-capabilities.txt"
FINAL_CHAIN_AUDIT="$SCRIPT_DIR/audit-a33-first-rootfs-chain-final.sh"
FINAL_CHAIN_REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-final-audit.txt"
STAGE_SCRIPT="$SCRIPT_DIR/stage-a33-userdata-rootfs-in-twrp.sh"
STAGE_REPORT="$PORT_ROOT/build/a33-userdata-rootfs-stage.txt"
REPORT="$PORT_ROOT/build/a33-first-rootfs-transport-final-audit.txt"
DETAILS="$PORT_ROOT/build/a33-first-rootfs-transport-final-audit-details.txt"

for command in \
    bash sha256sum awk grep date tee python3 ssh timeout mktemp readlink \
    seq sleep cat rm git; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$SELF" "$COMMAND_AUDIT" "$FINAL_CHAIN_AUDIT" "$STAGE_SCRIPT"; do
    [[ -f "$required" ]] || {
        echo "Missing required script: $required" >&2
        exit 1
    }
done

mkdir -p "$PORT_ROOT/build"
: > "$DETAILS"

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

for script in "${SCRIPTS[@]}"; do
    bash -n "$SCRIPT_DIR/$script"
    printf 'syntax=passed sha256=%s script=%s\n' \
        "$(sha256sum "$SCRIPT_DIR/$script" | awk '{print $1}')" "$script" \
        >> "$DETAILS"
done
bash -n "$SELF"
bash -n "$FINAL_CHAIN_AUDIT"

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck -S error "$SELF" "$FINAL_CHAIN_AUDIT" "${SCRIPTS[@]/#/$SCRIPT_DIR/}"
    echo "shellcheck_error_severity=passed" >> "$DETAILS"
else
    echo "shellcheck=not-installed-syntax-and-contract-checks-used" >> "$DETAILS"
fi

# Prove every host, ADB, TWRP and rootfs command used later before any image is
# staged and before any persistent partition can be written.
bash "$COMMAND_AUDIT" | tee -a "$DETAILS"

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

if [[ "$(value "$COMMAND_REPORT" command_capability_audit_status)" != passed || \
      "$(value "$COMMAND_REPORT" host_required_commands_status)" != passed || \
      "$(value "$COMMAND_REPORT" adb_shell_probe)" != passed || \
      "$(value "$COMMAND_REPORT" adb_push_binary_probe)" != passed || \
      "$(value "$COMMAND_REPORT" adb_exec_out_binary_probe)" != passed || \
      "$(value "$COMMAND_REPORT" adb_exec_in_used)" != no || \
      "$(value "$COMMAND_REPORT" adb_help_feature_detection_used)" != no || \
      "$(value "$COMMAND_REPORT" twrp_required_commands_status)" != passed || \
      "$(value "$COMMAND_REPORT" twrp_command_option_probes)" != passed || \
      "$(value "$COMMAND_REPORT" rootfs_required_runtime_commands)" != passed || \
      "$(value "$COMMAND_REPORT" persistent_phone_partition_writes)" != no ]]; then
    echo "REFUSING: command capability audit did not pass" >&2
    cat "$COMMAND_REPORT" >&2
    exit 1
fi

# Re-run every existing non-destructive chain and rescue gate against the
# changed deployment and execution scripts.
bash "$FINAL_CHAIN_AUDIT" | tee -a "$DETAILS"

if [[ "$(value "$FINAL_CHAIN_REPORT" final_audit_status)" != passed || \
      "$(value "$FINAL_CHAIN_REPORT" audit_status)" != passed || \
      "$(value "$FINAL_CHAIN_REPORT" final_phone_writes)" != no || \
      "$(value "$FINAL_CHAIN_REPORT" private_backup_checksums)" != passed || \
      "$(value "$FINAL_CHAIN_REPORT" rescue_assets_status)" != passed ]]; then
    echo "REFUSING: existing complete-chain audit did not pass" >&2
    cat "$FINAL_CHAIN_REPORT" >&2
    exit 1
fi

python3 - <<'PY'
import socket
assert socket.AF_INET
print("python_socket_support=passed")
PY
echo "python_socket_support=passed" >> "$DETAILS"

ssh -G \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/dev/null \
    127.0.0.1 >/dev/null
echo "ssh_strict_host_key_accept_new=passed" >> "$DETAILS"

# The current observer uses Bash /dev/tcp. Test it against a temporary local
# listener rather than assuming this optional Bash feature exists.
TCP_DIR="$(mktemp -d)"
TCP_PORT_FILE="$TCP_DIR/port"
cleanup_tcp() {
    if [[ -n "${TCP_SERVER_PID:-}" ]]; then
        kill "$TCP_SERVER_PID" >/dev/null 2>&1 || true
        wait "$TCP_SERVER_PID" >/dev/null 2>&1 || true
    fi
    rm -rf "$TCP_DIR"
}
trap cleanup_tcp EXIT

python3 - "$TCP_PORT_FILE" <<'PY' &
from pathlib import Path
import socket
import sys

path = Path(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    server.settimeout(10)
    path.write_text(str(server.getsockname()[1]), encoding="ascii")
    connection, _ = server.accept()
    with connection:
        connection.settimeout(3)
        connection.recv(1)
PY
TCP_SERVER_PID=$!

for _ in $(seq 1 100); do
    [[ -s "$TCP_PORT_FILE" ]] && break
    sleep 0.05
done
[[ -s "$TCP_PORT_FILE" ]] || {
    echo "REFUSING: local TCP capability test did not start" >&2
    exit 1
}
TCP_PORT="$(cat "$TCP_PORT_FILE")"
timeout 3 bash -c "exec 3<>/dev/tcp/127.0.0.1/$TCP_PORT; printf x >&3; exec 3>&-"
wait "$TCP_SERVER_PID"
TCP_SERVER_PID=""
echo "bash_dev_tcp_support=passed" >> "$DETAILS"

# Prove the exact ADB/TWRP pair can transfer the complete image in both
# directions without exec-in. The staged file remains in volatile /tmp.
bash "$STAGE_SCRIPT" | tee -a "$DETAILS"

if [[ "$(value "$STAGE_REPORT" staging_status)" != passed || \
      "$(value "$STAGE_REPORT" adb_exec_in_required)" != no || \
      "$(value "$STAGE_REPORT" adb_push_full_image)" != passed || \
      "$(value "$STAGE_REPORT" adb_exec_out_full_readback)" != passed || \
      "$(value "$STAGE_REPORT" source_sha256)" != "$(value "$STAGE_REPORT" remote_sha256)" || \
      "$(value "$STAGE_REPORT" source_sha256)" != "$(value "$STAGE_REPORT" full_readback_sha256)" || \
      "$(value "$STAGE_REPORT" persistent_phone_writes)" != no ]]; then
    echo "REFUSING: full-image ADB staging/readback did not pass" >&2
    cat "$STAGE_REPORT" >&2
    exit 1
fi

COMMAND_REPORT_SHA="$(sha256sum "$COMMAND_REPORT" | awk '{print $1}')"
FINAL_CHAIN_SHA="$(sha256sum "$FINAL_CHAIN_REPORT" | awk '{print $1}')"
STAGE_REPORT_SHA="$(sha256sum "$STAGE_REPORT" | awk '{print $1}')"
IMAGE_SHA="$(value "$STAGE_REPORT" source_sha256)"
IMAGE_SIZE="$(value "$STAGE_REPORT" source_size)"

{
    echo "created=$(date -Ins)"
    echo "operation=audit-a33-first-rootfs-real-transport"
    echo "repo_commit=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "transport_audit_script_sha256=$(sha256sum "$SELF" | awk '{print $1}')"
    echo "command_audit_script_sha256=$(sha256sum "$COMMAND_AUDIT" | awk '{print $1}')"
    echo "command_capability_report_sha256=$COMMAND_REPORT_SHA"
    echo "command_capability_audit_status=passed"
    echo "allowed_adb_subcommands=$(value "$COMMAND_REPORT" adb_allowed_subcommands)"
    echo "actual_adb_subcommands=$(value "$COMMAND_REPORT" adb_actual_subcommands)"
    echo "twrp_required_commands_status=passed"
    echo "final_chain_audit_script_sha256=$(sha256sum "$FINAL_CHAIN_AUDIT" | awk '{print $1}')"
    echo "final_chain_audit_report_sha256=$FINAL_CHAIN_SHA"
    echo "final_chain_audit_status=passed"
    echo "stage_script_sha256=$(sha256sum "$STAGE_SCRIPT" | awk '{print $1}')"
    echo "stage_report_sha256=$STAGE_REPORT_SHA"
    echo "staging_status=passed"
    echo "userdata_image_sha256=$IMAGE_SHA"
    echo "userdata_image_size=$IMAGE_SIZE"
    echo "remote_staged_image=$(value "$STAGE_REPORT" remote_image)"
    echo "remote_staged_sha256=$(value "$STAGE_REPORT" remote_sha256)"
    echo "adb_transport=push-plus-exec-out"
    echo "adb_exec_in_required=no"
    echo "adb_push_full_image=passed"
    echo "adb_exec_out_full_readback=passed"
    echo "python_socket_support=passed"
    echo "bash_dev_tcp_support=passed"
    echo "ssh_strict_host_key_accept_new=passed"
    echo "private_backup_checksums=$(value "$FINAL_CHAIN_REPORT" private_backup_checksums)"
    echo "rescue_assets_status=$(value "$FINAL_CHAIN_REPORT" rescue_assets_status)"
    echo "deploy_script_sha256=$(sha256sum "$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh" | awk '{print $1}')"
    echo "execute_script_sha256=$(sha256sum "$SCRIPT_DIR/execute-a33-first-rootfs-deployment.sh" | awk '{print $1}')"
    echo "flash_script_sha256=$(sha256sum "$SCRIPT_DIR/flash-a33-u0g-after-userdata-deploy.sh" | awk '{print $1}')"
    echo "observe_script_sha256=$(sha256sum "$SCRIPT_DIR/boot-observe-a33-first-rootfs.sh" | awk '{print $1}')"
    echo "live_collector_sha256=$(sha256sum "$SCRIPT_DIR/collect-a33-first-rootfs-live.sh" | awk '{print $1}')"
    echo "failure_collector_sha256=$(sha256sum "$SCRIPT_DIR/collect-a33-first-rootfs-previous-boot.sh" | awk '{print $1}')"
    echo "restore_script_sha256=$(sha256sum "$SCRIPT_DIR/restore-a33-twrp-odin.sh" | awk '{print $1}')"
    echo "persistent_phone_partition_writes=no"
    echo "volatile_twrp_tmpfs_write=yes"
    echo "transport_final_audit_status=passed"
} | tee "$REPORT"

cleanup_tcp
trap - EXIT

echo
echo "A33 real transport and complete future-script audit passed."
echo "Report:  $REPORT"
echo "Details: $DETAILS"
echo "The fully verified rootfs remains staged in volatile TWRP /tmp."
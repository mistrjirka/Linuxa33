#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IMAGE_LINK="$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img"
IMAGE_MANIFEST_LINK="$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt"
HANDOFF_REPORT="$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt"
BACKUP_ROOT="$PORT_ROOT/build/private-backups"
REPORT="$PORT_ROOT/build/a33-first-rootfs-chain-audit.txt"
DETAILS="$PORT_ROOT/build/a33-first-rootfs-chain-audit-details.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA_RESOLVED="/dev/block/sda36"
EXPECTED_USERDATA_BYTES="114240258048"

SCRIPTS=(
    verify-a33-u0g-unified-root-handoff.sh
    prepare-a33-userdata-rootfs-image.sh
    backup-a33-before-userdata-repurpose.sh
    deploy-a33-rootfs-to-userdata.sh
    flash-a33-u0g-after-userdata-deploy.sh
    boot-observe-a33-first-rootfs.sh
    collect-a33-first-rootfs-live.sh
    verify-a33-twrp-rescue-assets.sh
    restore-a33-twrp-odin.sh
    collect-a33-first-rootfs-previous-boot.sh
    collect-a33-previous-boot.sh
    prepare-a33-cache-boot-image.sh
    complete-a33-internal-layout-preflight.sh
)

for command in "$ADB" bash sha256sum stat awk grep find sort realpath date mkdir; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

mkdir -p "$PORT_ROOT/build"
: > "$DETAILS"

echo "=== Bash syntax check for complete remaining chain ===" | tee -a "$DETAILS"
for script in "${SCRIPTS[@]}"; do
    path="$SCRIPT_DIR/$script"
    [[ -f "$path" ]] || {
        echo "REFUSING: required script is missing: $path" >&2
        exit 1
    }
    bash -n "$path"
    printf 'syntax=passed sha256=%s script=%s\n' \
        "$(sha256sum "$path" | awk '{print $1}')" "$script" \
        | tee -a "$DETAILS"
done

if command -v shellcheck >/dev/null 2>&1; then
    echo "=== Optional ShellCheck ===" | tee -a "$DETAILS"
    if shellcheck -S error "${SCRIPTS[@]/#/$SCRIPT_DIR/}" >> "$DETAILS" 2>&1; then
        echo "shellcheck_error_severity=passed" | tee -a "$DETAILS"
    else
        echo "REFUSING: ShellCheck found error-severity findings" >&2
        tail -n 100 "$DETAILS" >&2
        exit 1
    fi
else
    echo "shellcheck=not-installed-syntax-and-contract-checks-used" | tee -a "$DETAILS"
fi

# Obsolete cache paths must remain harmless stubs.
for obsolete in prepare-a33-cache-boot-image.sh complete-a33-internal-layout-preflight.sh; do
    path="$SCRIPT_DIR/$obsolete"
    grep -Fq 'exit 2' "$path" || {
        echo "REFUSING: obsolete cache script is not a refusing stub: $obsolete" >&2
        exit 1
    }
    if grep -Eq '(^|[[:space:]])dd[[:space:]]|exec-in|of=/dev/block' "$path"; then
        echo "REFUSING: obsolete cache script still contains a write primitive: $obsolete" >&2
        exit 1
    fi
done

# Static destructive target allowlist.
grep -Fq 'TARGET="/dev/block/by-name/userdata"' "$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh" || {
    echo "REFUSING: userdata deploy target is not explicit" >&2
    exit 1
}
grep -Fq 'Only userdata will be overwritten' "$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh" || {
    echo "REFUSING: userdata isolation contract text is missing" >&2
    exit 1
}
grep -Fq 'TARGET="/dev/block/by-name/recovery"' "$SCRIPT_DIR/flash-a33-u0g-after-userdata-deploy.sh" || {
    echo "REFUSING: recovery flash target is not explicit" >&2
    exit 1
}

# Execute the host-only exact U0g handoff verifier.
bash "$SCRIPT_DIR/verify-a33-u0g-unified-root-handoff.sh" | tee -a "$DETAILS"

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}
if [[ "$(value "$HANDOFF_REPORT" verification_status)" != passed || \
      "$(value "$HANDOFF_REPORT" cache_partition_required)" != no || \
      "$(value "$HANDOFF_REPORT" pmos_root_discovery)" != yes || \
      "$(value "$HANDOFF_REPORT" switch_root_present)" != yes ]]; then
    echo "REFUSING: unified handoff verifier report failed" >&2
    cat "$HANDOFF_REPORT" >&2
    exit 1
fi
HANDOFF_REPORT_SHA="$(sha256sum "$HANDOFF_REPORT" | awk '{print $1}')"

IMAGE="$(realpath -e "$IMAGE_LINK" 2>/dev/null || true)"
IMAGE_MANIFEST="$(realpath -e "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$IMAGE" || -z "$IMAGE_MANIFEST" ]]; then
    echo "REFUSING: current userdata image or manifest is missing" >&2
    exit 1
fi
IMAGE_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_SIZE="$(stat -Lc '%s' "$IMAGE")"
if [[ "$(value "$IMAGE_MANIFEST" preparation_status)" != passed || \
      "$(value "$IMAGE_MANIFEST" deployment_sha256)" != "$IMAGE_SHA" || \
      "$(value "$IMAGE_MANIFEST" deployment_size)" != "$IMAGE_SIZE" || \
      "$(value "$IMAGE_MANIFEST" root_type)" != ext4 || \
      "$(value "$IMAGE_MANIFEST" root_label)" != pmOS_root || \
      "$(value "$IMAGE_MANIFEST" fstab_root_only)" != yes ]]; then
    echo "REFUSING: current userdata image does not match its validated manifest" >&2
    exit 1
fi
IMAGE_MANIFEST_SHA="$(sha256sum "$IMAGE_MANIFEST" | awk '{print $1}')"

PREFLIGHT_DIR="$(
    find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d \
        -name 'a33-before-userdata-repurpose-*' -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr \
    | awk 'NR==1 {sub(/^[^ ]+ /, ""); print}'
)"
PREFLIGHT_DIR="$(realpath -e "$PREFLIGHT_DIR" 2>/dev/null || true)"
PREFLIGHT_MANIFEST="$PREFLIGHT_DIR/manifest.txt"
PREFLIGHT_SUMS="$PREFLIGHT_DIR/SHA256SUMS"
if [[ -z "$PREFLIGHT_DIR" || ! -f "$PREFLIGHT_MANIFEST" || ! -f "$PREFLIGHT_SUMS" ]]; then
    echo "REFUSING: private backup preflight is missing" >&2
    exit 1
fi
if [[ "$(value "$PREFLIGHT_MANIFEST" backup_status)" != passed || \
      "$(value "$PREFLIGHT_MANIFEST" deployment_sha256)" != "$IMAGE_SHA" || \
      "$(value "$PREFLIGHT_MANIFEST" deployment_size)" != "$IMAGE_SIZE" || \
      "$(value "$PREFLIGHT_MANIFEST" userdata_resolved)" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$(value "$PREFLIGHT_MANIFEST" userdata_bytes)" != "$EXPECTED_USERDATA_BYTES" ]]; then
    echo "REFUSING: private backup preflight does not match current image/target" >&2
    exit 1
fi
(
    cd /
    sha256sum -c "$PREFLIGHT_SUMS"
) >> "$DETAILS" 2>&1 || {
    echo "REFUSING: private backup checksum verification failed" >&2
    tail -n 100 "$DETAILS" >&2
    exit 1
}
PREFLIGHT_MANIFEST_SHA="$(sha256sum "$PREFLIGHT_MANIFEST" | awk '{print $1}')"
PREFLIGHT_SUMS_SHA="$(sha256sum "$PREFLIGHT_SUMS" | awk '{print $1}')"

until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done
LIVE="$(
    "$ADB" shell sh -s 2>/dev/null <<'SH' | tr -d '\r'
set -u
target=/dev/block/by-name/userdata
resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"
echo "userdata_resolved=$resolved"
echo "userdata_bytes=$(blockdev --getsize64 "$target" 2>/dev/null || true)"
echo "userdata_readonly=$(blockdev --getro "$target" 2>/dev/null || true)"
echo "mount_users_begin"
awk '{print $1, $2}' /proc/mounts 2>/dev/null | while read -r source mountpoint; do
    source_resolved="$(readlink -f "$source" 2>/dev/null || true)"
    if [ "$source" = "$target" ] || [ "$source" = "$resolved" ] || [ "$source_resolved" = "$resolved" ]; then
        echo "$source $mountpoint"
    fi
done
echo "mount_users_end"
echo "dm_users_begin"
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    if find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | grep -qx "${resolved##*/}"; then
        echo "${dm##*/}:$(cat "$dm/dm/name" 2>/dev/null || true)"
    fi
done
echo "dm_users_end"
SH
)"
live_value() {
    local key="$1"
    printf '%s\n' "$LIVE" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}
section() {
    local name="$1"
    printf '%s\n' "$LIVE" | awk -v begin="${name}_begin" -v end="${name}_end" '
        $0==begin {inside=1; next}
        $0==end {inside=0}
        inside && NF {print}
    '
}

if [[ "$(live_value recovery_sha)" != "$KNOWN_TWRP_SHA256" || \
      "$(live_value userdata_resolved)" != "$EXPECTED_USERDATA_RESOLVED" || \
      "$(live_value userdata_bytes)" != "$EXPECTED_USERDATA_BYTES" || \
      "$(live_value userdata_readonly)" != 0 || \
      -n "$(section mount_users)" || \
      -n "$(section dm_users)" ]]; then
    echo "REFUSING: live TWRP/userdata state is not safe for deployment" >&2
    printf '%s\n' "$LIVE" >&2
    exit 1
fi
printf '%s\n' "$LIVE" >> "$DETAILS"

DEPLOY_SHA="$(sha256sum "$SCRIPT_DIR/deploy-a33-rootfs-to-userdata.sh" | awk '{print $1}')"
FLASH_SHA="$(sha256sum "$SCRIPT_DIR/flash-a33-u0g-after-userdata-deploy.sh" | awk '{print $1}')"
OBSERVE_SHA="$(sha256sum "$SCRIPT_DIR/boot-observe-a33-first-rootfs.sh" | awk '{print $1}')"
LIVE_COLLECT_SHA="$(sha256sum "$SCRIPT_DIR/collect-a33-first-rootfs-live.sh" | awk '{print $1}')"
FAIL_COLLECT_SHA="$(sha256sum "$SCRIPT_DIR/collect-a33-first-rootfs-previous-boot.sh" | awk '{print $1}')"
RESTORE_SHA="$(sha256sum "$SCRIPT_DIR/restore-a33-twrp-odin.sh" | awk '{print $1}')"

{
    echo "created=$(date -Ins)"
    echo "operation=audit-complete-first-rootfs-chain"
    echo "repo_commit=$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "bash_syntax_all=passed"
    echo "obsolete_cache_scripts=refusing-stubs"
    echo "u0g_handoff_report_sha256=$HANDOFF_REPORT_SHA"
    echo "u0g_handoff_status=passed"
    echo "userdata_image=$IMAGE"
    echo "userdata_image_sha256=$IMAGE_SHA"
    echo "userdata_image_size=$IMAGE_SIZE"
    echo "userdata_image_manifest_sha256=$IMAGE_MANIFEST_SHA"
    echo "private_backup_dir=$PREFLIGHT_DIR"
    echo "private_backup_manifest_sha256=$PREFLIGHT_MANIFEST_SHA"
    echo "private_backup_sha256sums_sha256=$PREFLIGHT_SUMS_SHA"
    echo "private_backup_checksums=passed"
    echo "twrp_recovery_sha256=$(live_value recovery_sha)"
    echo "userdata_resolved=$(live_value userdata_resolved)"
    echo "userdata_bytes=$(live_value userdata_bytes)"
    echo "userdata_unmounted=yes"
    echo "userdata_device_mapper_users=none"
    echo "deploy_script_sha256=$DEPLOY_SHA"
    echo "flash_script_sha256=$FLASH_SHA"
    echo "observe_script_sha256=$OBSERVE_SHA"
    echo "live_collector_sha256=$LIVE_COLLECT_SHA"
    echo "failure_collector_sha256=$FAIL_COLLECT_SHA"
    echo "restore_script_sha256=$RESTORE_SHA"
    echo "phone_writes=no"
    echo "audit_status=passed"
} | tee "$REPORT"

echo
echo "Complete A33 first-rootfs chain audit passed."
echo "Report:  $REPORT"
echo "Details: $DETAILS"

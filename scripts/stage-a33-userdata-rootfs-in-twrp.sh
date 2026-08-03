#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
IMAGE_LINK="${IMAGE_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img}"
IMAGE_MANIFEST_LINK="${IMAGE_MANIFEST_LINK:-$PORT_ROOT/build/userdata-rootfs-images/current/manifest.txt}"
REMOTE_IMAGE="${REMOTE_IMAGE:-/tmp/a33x-userdata-pmos-root.img}"
REPORT="$PORT_ROOT/build/a33-userdata-rootfs-stage.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
TMP_MARGIN_BYTES=$((256 * 1024 * 1024))

for command in "$ADB" readlink sha256sum stat awk grep date mkdir; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}

IMAGE="$(readlink -f "$IMAGE_LINK" 2>/dev/null || true)"
MANIFEST="$(readlink -f "$IMAGE_MANIFEST_LINK" 2>/dev/null || true)"
if [[ -z "$IMAGE" || ! -f "$IMAGE" || -z "$MANIFEST" || ! -f "$MANIFEST" ]]; then
    echo "REFUSING: prepared userdata root image or manifest is missing" >&2
    exit 1
fi

IMAGE_SHA="$(sha256sum "$IMAGE" | awk '{print $1}')"
IMAGE_SIZE="$(stat -Lc '%s' "$IMAGE")"
if [[ "$(value "$MANIFEST" preparation_status)" != passed || \
      "$(value "$MANIFEST" deployment_sha256)" != "$IMAGE_SHA" || \
      "$(value "$MANIFEST" deployment_size)" != "$IMAGE_SIZE" || \
      "$(value "$MANIFEST" root_type)" != ext4 || \
      "$(value "$MANIFEST" root_label)" != pmOS_root ]]; then
    echo "REFUSING: userdata root image differs from its validated manifest" >&2
    exit 1
fi

mkdir -p "$PORT_ROOT/build"

echo "=== Wait for exact known-good TWRP ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

PRECHECK="$(
    "$ADB" shell sh -s -- "$REMOTE_IMAGE" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
remote="$1"
echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery | awk 'NR==1 {print $1}')"
echo "tmp_mount=$(awk '$2=="/tmp" {print $1 ":" $3 ":" $4; exit}' /proc/mounts 2>/dev/null || true)"
echo "tmp_available_kib=$(df -k /tmp 2>/dev/null | awk 'NR>1 {line=$0} END {print $(NF-2)}')"
rm -f "$remote"
[ ! -e "$remote" ]
echo "remote_path_clear=yes"
SH
)"

precheck_value() {
    local key="$1"
    printf '%s\n' "$PRECHECK" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}

RECOVERY_SHA="$(precheck_value recovery_sha)"
TMP_MOUNT="$(precheck_value tmp_mount)"
TMP_AVAILABLE_KIB="$(precheck_value tmp_available_kib)"
if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running exact known-good TWRP" >&2
    exit 1
fi
if [[ -z "$TMP_MOUNT" || ! "$TMP_AVAILABLE_KIB" =~ ^[0-9]+$ ]]; then
    echo "REFUSING: could not prove TWRP /tmp capacity" >&2
    printf '%s\n' "$PRECHECK" >&2
    exit 1
fi
TMP_AVAILABLE_BYTES=$((TMP_AVAILABLE_KIB * 1024))
TMP_REQUIRED_BYTES=$((IMAGE_SIZE + TMP_MARGIN_BYTES))
if (( TMP_AVAILABLE_BYTES < TMP_REQUIRED_BYTES )); then
    echo "REFUSING: TWRP /tmp lacks capacity for safe full-image staging" >&2
    echo "available=$TMP_AVAILABLE_BYTES required=$TMP_REQUIRED_BYTES" >&2
    exit 1
fi

echo "=== Push the complete rootfs image to volatile TWRP /tmp ==="
"$ADB" push "$IMAGE" "$REMOTE_IMAGE"

REMOTE_IDENTITY="$(
    "$ADB" shell sh -s -- "$REMOTE_IMAGE" 2>/dev/null <<'SH' | tr -d '\r'
set -eu
remote="$1"
[ -f "$remote" ]
echo "remote_size=$(stat -c '%s' "$remote")"
echo "remote_sha256=$(sha256sum "$remote" | awk 'NR==1 {print $1}')"
echo "remote_readable=$([ -r "$remote" ] && echo yes || echo no)"
SH
)"
remote_value() {
    local key="$1"
    printf '%s\n' "$REMOTE_IDENTITY" | awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}'
}
REMOTE_SIZE="$(remote_value remote_size)"
REMOTE_SHA="$(remote_value remote_sha256)"
REMOTE_READABLE="$(remote_value remote_readable)"
if [[ "$REMOTE_SIZE" != "$IMAGE_SIZE" || "$REMOTE_SHA" != "$IMAGE_SHA" || "$REMOTE_READABLE" != yes ]]; then
    "$ADB" shell "rm -f '$REMOTE_IMAGE'" >/dev/null 2>&1 || true
    echo "REFUSING: staged rootfs identity mismatch" >&2
    printf '%s\n' "$REMOTE_IDENTITY" >&2
    exit 1
fi

echo "=== Verify full binary reverse transport with adb exec-out ==="
READBACK_SHA="$(
    "$ADB" exec-out sh -c "dd if='$REMOTE_IMAGE' bs=1048576 2>/dev/null" \
    | sha256sum \
    | awk '{print $1}'
)"
if [[ "$READBACK_SHA" != "$IMAGE_SHA" ]]; then
    "$ADB" shell "rm -f '$REMOTE_IMAGE'" >/dev/null 2>&1 || true
    echo "REFUSING: adb exec-out full-image readback mismatch" >&2
    echo "expected=$IMAGE_SHA actual=$READBACK_SHA" >&2
    exit 1
fi

{
    echo "created=$(date -Ins)"
    echo "operation=stage-verified-rootfs-in-volatile-twrp-tmp"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "source_image=$IMAGE"
    echo "source_manifest=$MANIFEST"
    echo "source_size=$IMAGE_SIZE"
    echo "source_sha256=$IMAGE_SHA"
    echo "remote_image=$REMOTE_IMAGE"
    echo "remote_size=$REMOTE_SIZE"
    echo "remote_sha256=$REMOTE_SHA"
    echo "tmp_mount=$TMP_MOUNT"
    echo "tmp_available_bytes_before=$TMP_AVAILABLE_BYTES"
    echo "tmp_required_bytes=$TMP_REQUIRED_BYTES"
    echo "adb_transport=push-plus-exec-out"
    echo "adb_exec_in_required=no"
    echo "adb_push_full_image=passed"
    echo "adb_exec_out_full_readback=passed"
    echo "full_readback_sha256=$READBACK_SHA"
    echo "persistent_phone_writes=no"
    echo "volatile_tmpfs_write=yes"
    echo "staging_status=passed"
} | tee "$REPORT"

echo
echo "A33 rootfs staged and fully read back through the available ADB transport."
echo "Report: $REPORT"
echo "Remote: $REMOTE_IMAGE"
echo "No persistent phone partition was written."
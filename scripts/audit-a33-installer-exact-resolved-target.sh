#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-normal-rootfs}"
ZIP_LINK="$EXPORT_DIR/pmos-samsung-a33x.zip"
ADB="${ADB:-adb}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$PORT_ROOT/build/a33-installer-resolved-target-audit-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"
REMOTE_ROOT="/tmp/a33x-installer-resolver"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"

for command in "$ADB" unzip tar sha256sum stat awk grep sed find mktemp; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

ZIP="$(readlink -f "$ZIP_LINK" 2>/dev/null || true)"
if [[ -z "$ZIP" || ! -f "$ZIP" ]]; then
    echo "REFUSING: validated installer is missing: $ZIP_LINK" >&2
    exit 1
fi

mkdir -p "$OUT"/{host,installer,twrp}
STAGE="$(mktemp -d)"
cleanup() {
    rm -rf "$STAGE"
    "$ADB" shell "rm -rf '$REMOTE_ROOT'" >/dev/null 2>&1 || true
}
trap cleanup EXIT

unzip -q "$ZIP" 'chroot/*' -d "$STAGE"
unzip -p "$ZIP" chroot/install_options > "$OUT/installer/install_options.txt"
unzip -p "$ZIP" chroot/bin/pmos_install_functions > "$OUT/installer/pmos_install_functions.txt"
unzip -p "$ZIP" chroot/bin/pmos_install > "$OUT/installer/pmos_install.txt"

for required in \
    "$STAGE/chroot/bin/findfs" \
    "$STAGE/chroot/install_options" \
    "$STAGE/chroot/lib/ld-musl-aarch64.so.1"; do
    [[ -e "$required" ]] || {
        echo "REFUSING: installer resolver payload is missing: $required" >&2
        exit 1
    }
done

INSTALL_PARTITION="$(awk -F= '$1=="INSTALL_PARTITION" {gsub(/^[[:space:]'"'"']+|[[:space:]'"'"']+$/, "", $2); print $2; exit}' "$OUT/installer/install_options.txt")"
FLASH_KERNEL="$(awk -F= '$1=="FLASH_KERNEL" {gsub(/^[[:space:]'"'"']+|[[:space:]'"'"']+$/, "", $2); print $2; exit}' "$OUT/installer/install_options.txt")"

{
    echo "created=$(date -Ins)"
    echo "collection_policy=read-only-except-volatile-tmpfs-copy"
    echo "phone_required_mode=TWRP"
    echo "installer=$ZIP"
    echo "installer_sha256=$(sha256sum "$ZIP" | awk '{print $1}')"
    echo "install_partition=$INSTALL_PARTITION"
    echo "flash_kernel=$FLASH_KERNEL"
    echo "remote_payload=$REMOTE_ROOT"
    echo "persistent_phone_writes=no"
    echo "block_writes=no"
    echo "mount_operations=no"
} | tee "$OUT/manifest.txt"

# TWRP ADB quirk: do not use adb wait-for-device.
echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

RECOVERY_SHA="$($ADB shell 'sha256sum /dev/block/by-name/recovery 2>/dev/null' | awk 'NR==1 {print $1}' | tr -d '\r')"
if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" ]]; then
    echo "REFUSING: phone is not running the exact known-good TWRP" >&2
    echo "expected=$KNOWN_TWRP_SHA256" >&2
    echo "actual=${RECOVERY_SHA:-missing}" >&2
    exit 1
fi

echo "=== Copy installer resolver to volatile TWRP /tmp ==="
"$ADB" shell "rm -rf '$REMOTE_ROOT'; mkdir -p '$REMOTE_ROOT'"
"$ADB" push "$STAGE/chroot" "$REMOTE_ROOT/" > "$OUT/host/adb-push.txt" 2>&1

"$ADB" shell sh -s -- "$REMOTE_ROOT" "$INSTALL_PARTITION" > "$OUT/twrp/exact-resolution.txt" <<'SH'
set -u
ROOT="$1"
INSTALL_PARTITION="$2"
FIND_FS="$ROOT/chroot/bin/findfs"
LIBS="$ROOT/chroot/lib"

safe_readlink() {
    readlink -f "$1" 2>/dev/null || true
}

block_size() {
    blockdev --getsize64 "$1" 2>/dev/null || true
}

fstab="/etc/recovery.fstab"
[ -r "$fstab" ] || fstab="/etc/twrp.fstab"

echo "mode=TWRP"
echo "kernel=$(uname -r 2>/dev/null || true)"
echo "recovery_hash=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"
echo "install_partition=$INSTALL_PARTITION"
echo "fstab=$fstab"

echo "=== exact bundled findfs resolution ==="
findfs_output="$(LD_LIBRARY_PATH="$LIBS" "$FIND_FS" "PARTLABEL=$INSTALL_PARTITION" 2>&1 || true)"
echo "findfs_partlabel_output=$findfs_output"
findfs_target="$(printf '%s\n' "$findfs_output" | awk '/^\/dev\// {print; exit}')"
echo "findfs_target=$findfs_target"
echo "findfs_target_resolved=$(safe_readlink "$findfs_target")"
if [ -b "$findfs_target" ]; then
    echo "findfs_target_is_block=yes"
    echo "findfs_target_bytes=$(block_size "$findfs_target")"
else
    echo "findfs_target_is_block=no"
fi

echo "=== exact fstab fallback calculation ==="
src_column="$(awk '{for (i=1; i<=NF; ++i) { if ($i ~ /^\/dev/) {print i; exit;} }}' "$fstab" 2>/dev/null || true)"
[ -n "$src_column" ] || src_column=3
fstab_source="$(awk -v src="$src_column" -v part="$INSTALL_PARTITION" '!/^#/ && $0 ~ "(^|[[:space:]])/" part "([[:space:]]|$)" {print $src; exit}' "$fstab" 2>/dev/null || true)"
echo "fstab_source_column=$src_column"
echo "fstab_source=$fstab_source"
echo "fstab_source_readlink_fn=$(readlink -fn "$fstab_source" 2>/dev/null || true)"

echo "=== device-mapper links ==="
ls -la /dev/block/mapper 2>&1 || true
ls -la /dev/mapper 2>&1 || true

echo "=== device-mapper sysfs inventory ==="
for dm in /sys/block/dm-*; do
    [ -e "$dm" ] || continue
    base="${dm##*/}"
    name="$(cat "$dm/dm/name" 2>/dev/null || true)"
    uuid="$(cat "$dm/dm/uuid" 2>/dev/null || true)"
    sectors="$(cat "$dm/size" 2>/dev/null || true)"
    bytes=""
    [ -b "/dev/block/$base" ] && bytes="$(block_size "/dev/block/$base")"
    echo "dm_begin=$base"
    echo "dm_name=$name"
    echo "dm_uuid=$uuid"
    echo "dm_sectors=$sectors"
    echo "dm_bytes=$bytes"
    echo "dm_dev=/dev/block/$base"
    echo "dm_slaves=$(find "$dm/slaves" -mindepth 1 -maxdepth 1 -printf '%f ' 2>/dev/null || true)"
    echo "dm_end=$base"
done

echo "=== exact selected target classification ==="
resolved="$(safe_readlink "$findfs_target")"
base="${resolved##*/}"
dm_name=""
case "$base" in
    dm-[0-9]*) dm_name="$(cat "/sys/block/$base/dm/name" 2>/dev/null || true)" ;;
esac
echo "selected_target=$findfs_target"
echo "selected_resolved=$resolved"
echo "selected_base=$base"
echo "selected_dm_name=$dm_name"
case "$base:$dm_name" in
    dm-[0-9]*:system*) echo "selected_class=dynamic-logical-system" ;;
    dm-[0-9]*:*) echo "selected_class=device-mapper-other" ;;
    :*) echo "selected_class=unresolved" ;;
    *) echo "selected_class=physical-or-standalone-block" ;;
esac

echo "=== boot target resolution ==="
boot_findfs="$(LD_LIBRARY_PATH="$LIBS" "$FIND_FS" PARTLABEL=boot 2>&1 || true)"
echo "boot_findfs_output=$boot_findfs"
boot_target="$(printf '%s\n' "$boot_findfs" | awk '/^\/dev\// {print; exit}')"
if [ -z "$boot_target" ]; then
    boot_target=/dev/block/by-name/boot
fi
echo "boot_target=$boot_target"
echo "boot_target_resolved=$(safe_readlink "$boot_target")"
echo "boot_target_bytes=$(block_size "$boot_target")"

echo "=== installer destructive operations (static contract) ==="
echo "partition_contract=mktable-msdos-on-selected-install-device"
echo "partition_contract=mkpart-boot-2048s-to-256M"
echo "partition_contract=mkpart-root-256M-to-100percent"
echo "root_contract=mkfs-ext4-on-selected-device-partition-2"
echo "boot_contract=mkfs-ext2-or-ext4-on-selected-device-partition-1"
echo "kernel_contract=dd-boot-img-to-boot-partition-when-FLASH_KERNEL-true"
SH

# Remove the volatile resolver immediately after capture.
"$ADB" shell "rm -rf '$REMOTE_ROOT'"

RESOLUTION="$OUT/twrp/exact-resolution.txt"
value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$RESOLUTION"
}

SELECTED_TARGET="$(value selected_target)"
SELECTED_RESOLVED="$(value selected_resolved)"
SELECTED_DM_NAME="$(value selected_dm_name)"
SELECTED_CLASS="$(value selected_class)"
SELECTED_BYTES="$(value findfs_target_bytes)"
BOOT_TARGET="$(value boot_target)"
BOOT_RESOLVED="$(value boot_target_resolved)"
BOOT_BYTES="$(value boot_target_bytes)"

DECISION="manual-review-required"
if [[ "$INSTALL_PARTITION" == system && "$SELECTED_CLASS" == dynamic-logical-system ]]; then
    DECISION="standard-recovery-zip-not-approved-for-dynamic-system-target"
elif [[ "$SELECTED_CLASS" == unresolved ]]; then
    DECISION="standard-recovery-zip-not-approved-target-unresolved"
fi

{
    echo "installer_sha256=$(sha256sum "$ZIP" | awk '{print $1}')"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "install_partition=$INSTALL_PARTITION"
    echo "flash_kernel=$FLASH_KERNEL"
    echo "selected_target=${SELECTED_TARGET:-unknown}"
    echo "selected_resolved=${SELECTED_RESOLVED:-unknown}"
    echo "selected_dm_name=${SELECTED_DM_NAME:-none}"
    echo "selected_class=${SELECTED_CLASS:-unknown}"
    echo "selected_bytes=${SELECTED_BYTES:-unknown}"
    echo "boot_target=${BOOT_TARGET:-unknown}"
    echo "boot_resolved=${BOOT_RESOLVED:-unknown}"
    echo "boot_bytes=${BOOT_BYTES:-unknown}"
    echo "persistent_phone_writes=no"
    echo "block_writes=no"
    echo "mount_operations=no"
    echo "decision=$DECISION"
} | tee "$OUT/summary.txt"

tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "A33 exact installer target audit collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Checksum:  $ARCHIVE.sha256"
echo "Upload the .tar.gz archive only."

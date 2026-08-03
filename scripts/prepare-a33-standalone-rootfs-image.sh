#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
PMBOOTSTRAP_WORK="${PMBOOTSTRAP_WORK:-$HOME/.local/var/pmbootstrap}"
ROOTFS="${ROOTFS:-$PMBOOTSTRAP_WORK/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-normal-rootfs-images}"
ARTIFACT_ROOT="${ARTIFACT_ROOT:-$PORT_ROOT/build/rootfs-images}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
ARTIFACT_DIR="$ARTIFACT_ROOT/$TIMESTAMP"
CURRENT_LINK="$ARTIFACT_ROOT/current"
REPORT="$PORT_ROOT/build/a33-standalone-rootfs-image.txt"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
EXPECTED_INITRAMFS_SHA256="${EXPECTED_INITRAMFS_SHA256:-13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d}"
EXPECTED_HELPER_SHA256="${EXPECTED_HELPER_SHA256:-46cba296b6bddd03fba84e19174e19f00aa58e4453efcb4e138b27af3015c182}"
EXPECTED_HOOK03_SHA256="${EXPECTED_HOOK03_SHA256:-73cdce9c4e6f91ac0895505f2a82abd5b2561f22884e3cb74feb3dfc991d689b}"
EXPECTED_HOOK04_SHA256="${EXPECTED_HOOK04_SHA256:-5c3bc9720dad14d921b9a86d267c0da14f17e754507e1fd1516851530e0f6a8b}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

for command in pmbootstrap cp readlink sha256sum stat file blkid dumpe2fs resize2fs e2fsck debugfs gzip cpio find grep awk sed tar du; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

for required in \
    "$ROOTFS" \
    "$ROOTFS/boot/initramfs" \
    "$ROOTFS/usr/libexec/a33x-muic-switch-dynamic" \
    "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist-dynamic.sh"
do
    [[ -e "$required" ]] || {
        echo "REFUSING: validated normal rootfs input is missing: $required" >&2
        exit 1
    }
done

verify_sha() {
    local label="$1" path="$2" expected="$3" actual
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "REFUSING: $label SHA256 mismatch" >&2
        echo "expected=$expected" >&2
        echo "actual=$actual" >&2
        echo "path=$path" >&2
        exit 1
    fi
}

verify_sha initramfs "$ROOTFS/boot/initramfs" "$EXPECTED_INITRAMFS_SHA256"
verify_sha helper "$ROOTFS/usr/libexec/a33x-muic-switch-dynamic" "$EXPECTED_HELPER_SHA256"
verify_sha hook03 "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch-dynamic.sh" "$EXPECTED_HOOK03_SHA256"
verify_sha hook04 "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist-dynamic.sh" "$EXPECTED_HOOK04_SHA256"

for package in \
    postmarketos-mkinitfs-hook-a33x-watchdog \
    postmarketos-mkinitfs-hook-a33x-usbpd \
    postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic \
    postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic \
    openssh networkmanager networkmanager-cli networkmanager-wifi wpa_supplicant
do
    pmbootstrap chroot -r -- apk info -e "$package" >/dev/null || {
        echo "REFUSING: current rootfs is missing package: $package" >&2
        exit 1
    }
done

for service in sshd networkmanager; do
    find "$ROOTFS/etc/runlevels" -type l -name "$service" -print -quit | grep -q . || {
        echo "REFUSING: current rootfs does not enable service: $service" >&2
        exit 1
    }
done

mkdir -p "$PORT_ROOT/build" "$ARTIFACT_ROOT"
{
    echo "created=$(date -Ins)"
    echo "repo_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "operation=host-side-standalone-rootfs-image-generation"
    echo "phone_required=no"
    echo "phone_partition_writes=no"
    echo "rootfs=$ROOTFS"
    echo "export_dir=$EXPORT_DIR"
    echo "artifact_dir=$ARTIFACT_DIR"
} | tee "$REPORT"

echo "=== Generate standalone postmarketOS disk/root/boot images on host ==="
echo "This may request the password for user 'jirka' again. It does not access or write the phone."
pmbootstrap install

rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"

resolve_required() {
    local link="$1" label="$2" resolved
    resolved="$(readlink -f "$link" 2>/dev/null || true)"
    if [[ -z "$resolved" || ! -f "$resolved" ]]; then
        echo "REFUSING: pmbootstrap export did not produce $label" >&2
        echo "link=$link" >&2
        echo "resolved=${resolved:-missing}" >&2
        exit 1
    fi
    printf '%s\n' "$resolved"
}

ROOT_SOURCE="$(resolve_required "$EXPORT_DIR/samsung-a33x-root.img" root-image)"
BOOT_SOURCE="$(resolve_required "$EXPORT_DIR/samsung-a33x-boot.img" boot-image)"
COMBINED_SOURCE="$(resolve_required "$EXPORT_DIR/samsung-a33x.img" combined-image)"

mkdir -p "$ARTIFACT_DIR"
cp --reflink=auto --sparse=always --dereference "$ROOT_SOURCE" "$ARTIFACT_DIR/samsung-a33x-root.img"
cp --reflink=auto --sparse=always --dereference "$BOOT_SOURCE" "$ARTIFACT_DIR/samsung-a33x-boot.img"

ROOT_IMAGE="$ARTIFACT_DIR/samsung-a33x-root.img"
BOOT_IMAGE="$ARTIFACT_DIR/samsung-a33x-boot.img"

[[ "$(blkid -p -s TYPE -o value "$ROOT_IMAGE" 2>/dev/null || true)" == ext4 ]] || {
    echo "REFUSING: durable root image is not ext4" >&2
    file "$ROOT_IMAGE" >&2 || true
    blkid -p "$ROOT_IMAGE" >&2 || true
    exit 1
}

E2FSCK_RC=0
e2fsck -fn "$ROOT_IMAGE" > "$ARTIFACT_DIR/root-e2fsck-read-only.txt" 2>&1 || E2FSCK_RC=$?
if [[ "$E2FSCK_RC" -ne 0 ]]; then
    echo "REFUSING: read-only e2fsck returned rc=$E2FSCK_RC" >&2
    cat "$ARTIFACT_DIR/root-e2fsck-read-only.txt" >&2
    exit 1
fi

require_ext4_path() {
    local image="$1" path="$2" label="$3" output
    output="$(debugfs -R "stat $path" "$image" 2>&1 || true)"
    if ! grep -q '^Inode:' <<<"$output"; then
        echo "REFUSING: durable root image lacks $label at $path" >&2
        echo "$output" >&2
        exit 1
    fi
}

require_ext4_path "$ROOT_IMAGE" /usr/sbin/sshd OpenSSH-server
require_ext4_path "$ROOT_IMAGE" /etc/runlevels/default/sshd enabled-sshd-service
require_ext4_path "$ROOT_IMAGE" /etc/runlevels/default/networkmanager enabled-NetworkManager-service
require_ext4_path "$ROOT_IMAGE" /usr/libexec/a33x-muic-switch-dynamic U0g-helper
require_ext4_path "$ROOT_IMAGE" /usr/share/mkinitfs/hooks/03-a33x-muic-switch-dynamic.sh U0g-hook03
require_ext4_path "$ROOT_IMAGE" /usr/share/mkinitfs/hooks/04-a33x-muic-persist-dynamic.sh U0g-hook04

ROOT_BLOCK_SIZE="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block size/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_BLOCK_COUNT="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Block count/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_FREE_BLOCKS="$(dumpe2fs -h "$ROOT_IMAGE" 2>/dev/null | awk -F: '$1 ~ /^Free blocks/ {gsub(/[[:space:]]/, "", $2); print $2; exit}')"
ROOT_MIN_BLOCKS="$(resize2fs -P "$ROOT_IMAGE" 2>&1 | sed -n 's/.*minimum size of the filesystem is \([0-9][0-9]*\).*/\1/p' | tail -n 1)"

for value_name in ROOT_BLOCK_SIZE ROOT_BLOCK_COUNT ROOT_FREE_BLOCKS ROOT_MIN_BLOCKS; do
    value="${!value_name}"
    [[ "$value" =~ ^[0-9]+$ ]] || {
        echo "REFUSING: could not calculate $value_name" >&2
        exit 1
    }
done

ROOT_USED_BYTES=$(( (ROOT_BLOCK_COUNT - ROOT_FREE_BLOCKS) * ROOT_BLOCK_SIZE ))
ROOT_MINIMUM_BYTES=$(( ROOT_MIN_BLOCKS * ROOT_BLOCK_SIZE ))
ROOT_APPARENT_BYTES="$(stat -Lc '%s' "$ROOT_IMAGE")"
ROOT_ALLOCATED_BYTES="$(du -B1 "$ROOT_IMAGE" | awk 'NR==1 {print $1}')"
ROOT_SHA256="$(sha256sum "$ROOT_IMAGE" | awk '{print $1}')"
BOOT_SHA256="$(sha256sum "$BOOT_IMAGE" | awk '{print $1}')"
ROOT_UUID="$(blkid -p -s UUID -o value "$ROOT_IMAGE" 2>/dev/null || true)"
ROOT_LABEL="$(blkid -p -s LABEL -o value "$ROOT_IMAGE" 2>/dev/null || true)"

{
    echo "source_root=$ROOT_SOURCE"
    echo "source_boot=$BOOT_SOURCE"
    echo "source_combined=$COMBINED_SOURCE"
    echo "root_image=$ROOT_IMAGE"
    echo "root_sha256=$ROOT_SHA256"
    echo "root_apparent_bytes=$ROOT_APPARENT_BYTES"
    echo "root_allocated_bytes=$ROOT_ALLOCATED_BYTES"
    echo "root_used_bytes=$ROOT_USED_BYTES"
    echo "root_minimum_bytes=$ROOT_MINIMUM_BYTES"
    echo "root_uuid=${ROOT_UUID:-none}"
    echo "root_label=${ROOT_LABEL:-none}"
    echo "boot_image=$BOOT_IMAGE"
    echo "boot_sha256=$BOOT_SHA256"
    echo "e2fsck_read_only_rc=$E2FSCK_RC"
    echo "embedded_modules_expected=$EXPECTED_MODULE_COUNT"
    echo "u0g_validation=passed"
    echo "openssh_validation=passed"
    echo "networkmanager_validation=passed"
    echo "phone_partition_writes=no"
    echo "preparation_status=passed"
} | tee -a "$REPORT" | tee "$ARTIFACT_DIR/manifest.txt" >/dev/null

ln -sfn "$TIMESTAMP" "$CURRENT_LINK"

printf '%s  %s\n' "$ROOT_SHA256" "$ROOT_IMAGE" > "$ROOT_IMAGE.sha256"
printf '%s  %s\n' "$BOOT_SHA256" "$BOOT_IMAGE" > "$BOOT_IMAGE.sha256"

echo
echo "A33 standalone rootfs image prepared and validated."
echo "Root image: $ROOT_IMAGE"
echo "Boot image: $BOOT_IMAGE"
echo "Current:    $CURRENT_LINK"
echo "Report:     $REPORT"
echo "No phone partition was written."

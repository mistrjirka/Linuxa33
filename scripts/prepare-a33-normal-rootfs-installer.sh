#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
PMBOOTSTRAP_WORK="${PMBOOTSTRAP_WORK:-$HOME/.local/var/pmbootstrap}"
ROOTFS="${ROOTFS:-$PMBOOTSTRAP_WORK/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-normal-rootfs}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
TYPEC_SHA256="${TYPEC_SHA256:-de92f9dc0d29d671bd20f42ad01688e0584eb8e43f6826ff2643e0767c814641}"
PDIC_SHA256="${PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"
I2C_DEV_SHA256="${I2C_DEV_SHA256:-7553147cc20782c1fd7f86cd7494166d7c97b44591cd7dd81a086f1be7a81654}"
HELPER_SHA256="${HELPER_SHA256:-46cba296b6bddd03fba84e19174e19f00aa58e4453efcb4e138b27af3015c182}"
HOOK03_SHA256="${HOOK03_SHA256:-73cdce9c4e6f91ac0895505f2a82abd5b2561f22884e3cb74feb3dfc991d689b}"
HOOK04_SHA256="${HOOK04_SHA256:-5c3bc9720dad14d921b9a86d267c0da14f17e754507e1fd1516851530e0f6a8b}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
DEVICE_PORT="$PMAPORTS/device/downstream/device-samsung-a33x"
LINUX_PORT="$PMAPORTS/device/downstream/linux-samsung-a33x"
MODULES_INITFS="$DEVICE_PORT/modules-initfs"
MODULES_TAR="$LINUX_PORT/modules.tar.gz"
REPORT="$PORT_ROOT/build/a33-normal-rootfs-installer.txt"

CUSTOM_PACKAGES=(
    postmarketos-mkinitfs-hook-a33x-watchdog
    postmarketos-mkinitfs-hook-a33x-usbpd
    postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic
    postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic
)

for command in pmbootstrap rsync tar gzip cpio sha256sum awk grep sed find file readelf modinfo python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

for required in \
    "$ROOTFS" \
    "$REPO_ROOT/pmaports/device/downstream/device-samsung-a33x/APKBUILD" \
    "$REPO_ROOT/pmaports/main/postmarketos-mkinitfs-hook-a33x-watchdog/APKBUILD" \
    "$REPO_ROOT/pmaports/main/postmarketos-mkinitfs-hook-a33x-usbpd/APKBUILD" \
    "$REPO_ROOT/pmaports/main/postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic/APKBUILD" \
    "$REPO_ROOT/pmaports/main/postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic/APKBUILD" \
    "$MODULES_INITFS" "$MODULES_TAR"
do
    [[ -e "$required" ]] || {
        echo "REFUSING: required U0g/rootfs input is missing: $required" >&2
        exit 1
    }
done

if ! grep -Eiq 'i2c[-_]dev' "$MODULES_INITFS"; then
    echo "REFUSING: guarded modules-initfs does not contain i2c_dev" >&2
    echo "Re-run scripts/prepare-u0g-muic-dynamic-initramfs.sh before this script." >&2
    exit 1
fi

# Sync only tracked device/hook package definitions. Do not overlay the Linux
# package directory: its proprietary modules.tar.gz was patched and verified by
# U0g preparation and must not be replaced by an older local payload.
echo "=== Sync confirmed device and U0g package definitions ==="
rsync -a "$REPO_ROOT/pmaports/device/downstream/device-samsung-a33x/" "$DEVICE_PORT/"
for package in "${CUSTOM_PACKAGES[@]}"; do
    mkdir -p "$PMAPORTS/main/$package"
    rsync -a "$REPO_ROOT/pmaports/main/$package/" "$PMAPORTS/main/$package/"
done

for package in "${CUSTOM_PACKAGES[@]}"; do
    grep -Fq "$package" "$DEVICE_PORT/APKBUILD" || {
        echo "REFUSING: device package does not depend on $package" >&2
        exit 1
    }
done
if ! grep -Fq 'postmarketos-mkinitfs-hook-debug-shell' "$DEVICE_PORT/APKBUILD"; then
    echo "REFUSING: device package lacks the inactive-by-default debug-shell rescue payload" >&2
    exit 1
fi

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

# Verify that the prebuilt kernel package still carries the exact U0g module
# payload before letting pmbootstrap rebuild the rootfs.
echo "=== Verify patched kernel module payload ==="
module_extract="$(mktemp -d)"
cleanup_module_extract() { rm -rf "$module_extract"; }
trap cleanup_module_extract EXIT

tar -xzf "$MODULES_TAR" -C "$module_extract"
find_one_module() {
    local regex="$1" label="$2"
    mapfile -t matches < <(find "$module_extract" -type f | grep -E "$regex" || true)
    if [[ "${#matches[@]}" -ne 1 ]]; then
        echo "REFUSING: expected exactly one $label module, found ${#matches[@]}" >&2
        printf '%s\n' "${matches[@]}" >&2
        exit 1
    fi
    printf '%s\n' "${matches[0]}"
}
TYPEC_MODULE="$(find_one_module '/usb_typec_manager\.ko$' usb_typec_manager)"
PDIC_MODULE="$(find_one_module '/pdic_notifier_module\.ko$' pdic_notifier_module)"
I2C_MODULE="$(find_one_module '/(i2c-dev|i2c_dev)\.ko$' i2c_dev)"
verify_sha usb_typec_manager "$TYPEC_MODULE" "$TYPEC_SHA256"
verify_sha pdic_notifier_module "$PDIC_MODULE" "$PDIC_SHA256"
verify_sha i2c_dev "$I2C_MODULE" "$I2C_DEV_SHA256"

if [[ "$(modinfo -F name "$I2C_MODULE")" != "i2c_dev" ]]; then
    echo "REFUSING: I2C module metadata does not identify i2c_dev" >&2
    exit 1
fi

rm -rf "$module_extract"
trap - EXIT

mkdir -p "$PORT_ROOT/build"
{
    echo "created=$(date -Ins)"
    echo "repo_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "pmaports=$PMAPORTS"
    echo "rootfs=$ROOTFS"
    echo "export_dir=$EXPORT_DIR"
    echo "operation=host-side-rootfs-and-android-recovery-installer-generation"
    echo "phone_partition_writes=no"
    echo "expected_modules=$EXPECTED_MODULE_COUNT"
    echo "expected_typec_sha256=$TYPEC_SHA256"
    echo "expected_pdic_sha256=$PDIC_SHA256"
    echo "expected_i2c_dev_sha256=$I2C_DEV_SHA256"
    echo "expected_helper_sha256=$HELPER_SHA256"
    echo "expected_hook03_sha256=$HOOK03_SHA256"
    echo "expected_hook04_sha256=$HOOK04_SHA256"
} | tee "$REPORT"

echo "=== Refresh checksums for confirmed local packages ==="
pmbootstrap checksum linux-samsung-a33x
for package in "${CUSTOM_PACKAGES[@]}"; do
    pmbootstrap checksum "$package"
done
pmbootstrap checksum device-samsung-a33x

echo "=== Build updated A33 device package ==="
pmbootstrap build --force device-samsung-a33x

echo "=== Generate host-side Android recovery installer ==="
echo "This command builds files on the host only. It does not write the phone."
pmbootstrap install --android-recovery-zip

rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"

ZIP_LINK="$EXPORT_DIR/pmos-samsung-a33x.zip"
ZIP="$(readlink -f "$ZIP_LINK" 2>/dev/null || true)"
if [[ -z "$ZIP" || ! -f "$ZIP" ]]; then
    echo "REFUSING: pmbootstrap did not produce a usable recovery installer" >&2
    echo "link=$ZIP_LINK" >&2
    echo "resolved=${ZIP:-missing}" >&2
    exit 1
fi

for package in "${CUSTOM_PACKAGES[@]}" postmarketos-mkinitfs-hook-debug-shell openssh networkmanager; do
    pmbootstrap chroot -r -- apk info -e "$package" >/dev/null || {
        echo "REFUSING: regenerated rootfs is missing package: $package" >&2
        exit 1
    }
done

for service in sshd networkmanager; do
    if ! find "$ROOTFS/etc/runlevels" -type l -name "$service" -print -quit | grep -q .; then
        echo "REFUSING: regenerated rootfs does not enable service: $service" >&2
        exit 1
    fi
done

INITRAMFS="$ROOTFS/boot/initramfs"
[[ -f "$INITRAMFS" ]] || {
    echo "REFUSING: regenerated rootfs initramfs is missing: $INITRAMFS" >&2
    exit 1
}

initramfs_extract="$(mktemp -d)"
cleanup_initramfs_extract() { rm -rf "$initramfs_extract"; }
trap cleanup_initramfs_extract EXIT
gzip -dc "$INITRAMFS" > "$initramfs_extract/initramfs.cpio"
(
    cd "$initramfs_extract"
    cpio -idmu --quiet < initramfs.cpio
)
rm -f "$initramfs_extract/initramfs.cpio"

for required in \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    usr/libexec/a33x-muic-switch-dynamic
do
    [[ -f "$initramfs_extract/$required" ]] || {
        echo "REFUSING: regenerated initramfs is missing $required" >&2
        exit 1
    }
done

verify_sha helper "$initramfs_extract/usr/libexec/a33x-muic-switch-dynamic" "$HELPER_SHA256"
verify_sha hook03 "$initramfs_extract/hooks/03-a33x-muic-switch-dynamic.sh" "$HOOK03_SHA256"
verify_sha hook04 "$initramfs_extract/hooks/04-a33x-muic-persist-dynamic.sh" "$HOOK04_SHA256"

if grep -Fq '/dev/i2c-2' "$initramfs_extract/hooks/03-a33x-muic-switch-dynamic.sh"; then
    echo "REFUSING: regenerated initramfs returned to a hardcoded I2C bus" >&2
    exit 1
fi
if ! grep -Fq 'controller="13860000.hsi2c"' "$initramfs_extract/hooks/03-a33x-muic-switch-dynamic.sh"; then
    echo "REFUSING: regenerated initramfs lacks physical-controller discovery" >&2
    exit 1
fi

module_count="$(find "$initramfs_extract/usr/lib/modules" -type f \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING: regenerated initramfs contains $module_count modules, expected $EXPECTED_MODULE_COUNT" >&2
    exit 1
fi

rm -rf "$initramfs_extract"
trap - EXIT

zip_sha="$(sha256sum "$ZIP" | awk '{print $1}')"
zip_size="$(stat -Lc '%s' "$ZIP")"
initramfs_sha="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
{
    echo "installer_link=$ZIP_LINK"
    echo "installer=$ZIP"
    echo "installer_size=$zip_size"
    echo "installer_sha256=$zip_sha"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_sha256=$initramfs_sha"
    echo "embedded_modules=$module_count"
    echo "u0g_physical_controller=13860000.hsi2c"
    echo "u0g_bus_policy=dynamic"
    echo "sshd_enabled=yes"
    echo "networkmanager_enabled=yes"
    echo "phone_partition_writes=no"
    echo "preparation_status=passed"
} | tee -a "$REPORT"

echo
echo "A33 normal-rootfs installer prepared and validated."
echo "Installer: $ZIP_LINK"
echo "Resolved:  $ZIP"
echo "SHA256:    $zip_sha"
echo "Report:    $REPORT"
echo "No phone partition was written."

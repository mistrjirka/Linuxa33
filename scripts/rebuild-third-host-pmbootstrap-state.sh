#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
PMBOOTSTRAP_WORK="${PMBOOTSTRAP_WORK:-$HOME/.local/var/pmbootstrap}"
ROOTFS="${ROOTFS:-$PMBOOTSTRAP_WORK/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_PORTS="$REPO_ROOT/pmaports"

for command in pmbootstrap git rsync python3 sha256sum gzip cpio; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

if ! PMAPORTS="$(pmbootstrap config aports 2>/dev/null)" || [[ -z "$PMAPORTS" ]]; then
    echo "pmbootstrap is not initialized. Run pmbootstrap init first." >&2
    exit 1
fi

DEVICE="$(pmbootstrap config device 2>/dev/null || true)"
if [[ "$DEVICE" != "samsung-a33x" ]]; then
    echo "REFUSING: pmbootstrap device must be samsung-a33x, found: ${DEVICE:-unset}" >&2
    exit 1
fi

for required in \
    "$PMAPORTS/.git" \
    "$LOCAL_PORTS/device/downstream/linux-samsung-a33x/APKBUILD" \
    "$PORT_ROOT/reference/twrp/recovery.img" \
    "$PORT_ROOT/unpacked/twrp-root/lib/modules/modules.load.recovery" \
    "$PORT_ROOT/build/u0b-embedded-modules.txt" \
    "$REPO_ROOT/scripts/prepare-safe-module-packages.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing reconstructed or repository prerequisite: $required" >&2
        exit 1
    fi
done

echo "=== Re-overlay Linuxa33 custom aports ==="
rsync -a "$LOCAL_PORTS/" "$PMAPORTS/"

for required in \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/APKBUILD" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/Image" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/samsung-a33x.dtb" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/recovery_dtbo" \
    "$PMAPORTS/device/downstream/device-samsung-a33x/APKBUILD" \
    "$PMAPORTS/main/postmarketos-mkinitfs-hook-a33x-watchdog/APKBUILD" \
    "$PMAPORTS/main/postmarketos-mkinitfs-hook-a33x-usbpd/APKBUILD"
do
    if [[ ! -f "$required" ]]; then
        echo "REFUSING: pmaports is missing required custom artifact: $required" >&2
        exit 1
    fi
done

echo "=== Generate original safe module packages ==="
A33X_PDIC_FACTORY_PATCH=0 \
    bash "$REPO_ROOT/scripts/prepare-safe-module-packages.sh"

for package in \
    linux-samsung-a33x \
    device-samsung-a33x \
    postmarketos-mkinitfs-hook-a33x-watchdog \
    postmarketos-mkinitfs-hook-a33x-usbpd
do
    echo "=== Checksum $package ==="
    pmbootstrap checksum "$package"
done

echo "=== Build A33 kernel and device packages ==="
pmbootstrap build --force linux-samsung-a33x
pmbootstrap build --force device-samsung-a33x

echo "=== Build proven recovery hooks ==="
pmbootstrap build --force postmarketos-mkinitfs-hook-a33x-watchdog
pmbootstrap build --force postmarketos-mkinitfs-hook-a33x-usbpd

echo "=== Create A33 rootfs chroot and installation artifacts ==="
pmbootstrap install

if [[ ! -d "$ROOTFS" ]]; then
    echo "REFUSING: pmbootstrap install did not create expected rootfs chroot: $ROOTFS" >&2
    exit 1
fi

echo "=== Install watchdog, USB-PD and debug-shell initramfs hooks ==="
pmbootstrap chroot -r -- apk add --upgrade \
    postmarketos-mkinitfs-hook-a33x-watchdog \
    postmarketos-mkinitfs-hook-a33x-usbpd \
    postmarketos-mkinitfs-hook-debug-shell

for required in \
    "$ROOTFS/usr/share/mkinitfs/hooks/01-a33x-watchdog.sh" \
    "$ROOTFS/usr/share/mkinitfs/hooks/02-a33x-usbpd-load.sh"
do
    if [[ ! -f "$required" ]]; then
        echo "REFUSING: required hook is absent from rebuilt rootfs: $required" >&2
        exit 1
    fi
done

if [[ ! -f "$ROOTFS/usr/share/mkinitfs/files/20-debug-shell.files" ]]; then
    echo "REFUSING: debug-shell files list is absent from rebuilt rootfs" >&2
    exit 1
fi

echo "=== Rebuild base initramfs ==="
pmbootstrap chroot -r -- sh -ec "depmod -a '$KREL'; mkinitfs"

if [[ ! -f "$ROOTFS/boot/initramfs" ]]; then
    echo "REFUSING: mkinitfs did not produce $ROOTFS/boot/initramfs" >&2
    exit 1
fi

rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"

INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    echo "REFUSING: pmbootstrap export did not produce $INITRAMFS" >&2
    exit 1
fi

echo "=== Verify rebuilt base initramfs ==="
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-driver.sh" "$INITRAMFS"
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-hook.sh" "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-initramfs-safety.py" --initramfs "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-module-activation.py" \
    --contracts "$REPO_ROOT/config/module-activation-contracts.tsv" \
    --repo-root "$REPO_ROOT" \
    --initramfs "$INITRAMFS"

{
    echo "status=complete"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "pmbootstrap_version=$(pmbootstrap --version)"
    echo "device=$DEVICE"
    echo "pmaports=$PMAPORTS"
    echo "rootfs=$ROOTFS"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
} | tee "$PORT_ROOT/build/third-host-pmbootstrap-state.txt"

echo
echo "Third-host pmbootstrap state rebuilt successfully."
echo "Next: run scripts/prepare-u0e-muic-switch-initramfs.sh."

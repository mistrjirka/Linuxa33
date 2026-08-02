#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"
BASE_MODULE_COUNT="${BASE_MODULE_COUNT:-66}"
ORIGINAL_PDIC_SHA256="${ORIGINAL_PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TYPEC_PATCHER="$REPO_ROOT/scripts/patch-typec-muic-none-mask.py"
PDIC_PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"
PACKAGE="postmarketos-mkinitfs-hook-a33x-muic-switch"
PACKAGE_SOURCE="$REPO_ROOT/pmaports/main/$PACKAGE"

for command in pmbootstrap sudo sha256sum gzip cpio cmp python3 depmod modinfo tar file readelf; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$ROOTFS" \
    "$TYPEC_PATCHER" \
    "$PDIC_PATCHER" \
    "$PACKAGE_SOURCE/APKBUILD" \
    "$PACKAGE_SOURCE/a33x-muic-switch.c" \
    "$PACKAGE_SOURCE/03-a33x-muic-switch.sh" \
    "$PACKAGE_SOURCE/03-a33x-muic-switch.files" \
    "$PORT_ROOT/build/u0b-embedded-modules.txt" \
    "$REPO_ROOT/scripts/prepare-u0d-typec-muic-none-initramfs.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
DPKG="$PMAPORTS/device/downstream/device-samsung-a33x"
PACKAGE_DEST="$PMAPORTS/main/$PACKAGE"
ROOTFS_MODULE_LIST="$ROOTFS/usr/share/mkinitfs/modules/00-device-samsung-a33x.modules"
ROOTFS_HELPER="$ROOTFS/usr/libexec/a33x-muic-switch"
ROOTFS_HOOK="$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch.sh"
ROOTFS_FILES_LIST="$ROOTFS/usr/share/mkinitfs/files/03-a33x-muic-switch.files"

for required in "$DPKG/APKBUILD" "$DPKG/modules-initfs"; do
    if [[ ! -f "$required" ]]; then
        echo "Missing device package artifact: $required" >&2
        exit 1
    fi
done

echo "=== Recreate and verify U0d base ==="
bash "$REPO_ROOT/scripts/prepare-u0d-typec-muic-none-initramfs.sh"

echo "=== Sync and build U0e helper package ==="
rm -rf "$PACKAGE_DEST"
mkdir -p "$(dirname "$PACKAGE_DEST")"
cp -a "$PACKAGE_SOURCE" "$PACKAGE_DEST"

pmbootstrap checksum "$PACKAGE"
pmbootstrap build --force "$PACKAGE"
pmbootstrap chroot -r -- apk add --upgrade "$PACKAGE"

for required in "$ROOTFS_HELPER" "$ROOTFS_HOOK" "$ROOTFS_FILES_LIST"; do
    if [[ ! -f "$required" ]]; then
        echo "REFUSING U0e: helper package file was not installed: $required" >&2
        exit 1
    fi
done
if [[ ! -x "$ROOTFS_HELPER" || ! -x "$ROOTFS_HOOK" ]]; then
    echo "REFUSING U0e: helper or hook is not executable" >&2
    exit 1
fi

helper_file="$(file -b "$ROOTFS_HELPER")"
if [[ "$helper_file" != *"ELF 64-bit"* || "$helper_file" != *"ARM aarch64"* || "$helper_file" != *"statically linked"* ]]; then
    echo "REFUSING U0e: helper is not a static AArch64 ELF" >&2
    echo "$helper_file" >&2
    exit 1
fi
if ! readelf -hW "$ROOTFS_HELPER" | grep -q 'Machine:.*AArch64'; then
    echo "REFUSING U0e: helper ELF machine is not AArch64" >&2
    exit 1
fi

if ! grep -q '/usr/libexec/a33x-muic-switch' "$ROOTFS_FILES_LIST"; then
    echo "REFUSING U0e: mkinitfs files list does not include the helper" >&2
    exit 1
fi

for required_text in \
    'bus=2 address=0x3e' \
    'expected-adapter=13860000.hsi2c' \
    '/sys/bus/i2c/devices/2-003e' \
    'success bus=2 address=0x3e ctrl1=0x17 switch=0x24'
do
    if ! grep -Fq "$required_text" "$ROOTFS_HOOK"; then
        echo "REFUSING U0e: hook contract text is missing: $required_text" >&2
        exit 1
    fi
done

BACKUP_DIR="$PORT_ROOT/build/u0e-rootfs-backup"
mkdir -p "$BACKUP_DIR"
if [[ ! -f "$BACKUP_DIR/modules-initfs.before-u0e" ]]; then
    cp -a "$DPKG/modules-initfs" "$BACKUP_DIR/modules-initfs.before-u0e"
fi
if [[ ! -f "$BACKUP_DIR/00-device-samsung-a33x.modules.before-u0e" ]]; then
    sudo cp -a "$ROOTFS_MODULE_LIST" "$BACKUP_DIR/00-device-samsung-a33x.modules.before-u0e"
    sudo chown "$(id -u):$(id -g)" "$BACKUP_DIR/00-device-samsung-a33x.modules.before-u0e"
fi

ensure_module_line() {
    local file_path="$1"
    local module_name="$2"
    if ! grep -qx "$module_name" "$file_path"; then
        printf '%s\n' "$module_name" >> "$file_path"
    fi
}

echo "=== Add only i2c_dev to the U0e initramfs module profile ==="
ensure_module_line "$DPKG/modules-initfs" i2c_dev
sudo sh -ec '
file_path="$1"
module_name="$2"
if ! grep -qx "$module_name" "$file_path"; then
    printf "%s\n" "$module_name" >> "$file_path"
fi
' sh "$ROOTFS_MODULE_LIST" i2c_dev

if grep -Eq '^(mfd_s2mu106|muic_s2mu106|muic_manager|cpif|exynos_bts)$' "$DPKG/modules-initfs"; then
    echo "REFUSING U0e: full MUIC/modem dependency entered modules-initfs" >&2
    grep -E '^(mfd_s2mu106|muic_s2mu106|muic_manager|cpif|exynos_bts)$' "$DPKG/modules-initfs" >&2
    exit 1
fi

STAGED_ROOT="$PORT_ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
ROOTFS_MODULE_ROOT="$ROOTFS/usr/lib/modules/$KREL"
STAGED_I2C_DEV="$(find "$STAGED_ROOT" -type f \( -name 'i2c-dev.ko' -o -name 'i2c_dev.ko' \) -print -quit)"
ROOTFS_I2C_DEV="$(find "$ROOTFS_MODULE_ROOT" -type f \( -name 'i2c-dev.ko' -o -name 'i2c_dev.ko' \) -print -quit)"

for required in "$STAGED_I2C_DEV" "$ROOTFS_I2C_DEV"; do
    if [[ -z "$required" || ! -f "$required" ]]; then
        echo "REFUSING U0e: i2c_dev module artifact is missing: $required" >&2
        exit 1
    fi
done
if [[ "$(modinfo -F name "$STAGED_I2C_DEV")" != "i2c_dev" ]]; then
    echo "REFUSING U0e: staged I2C character module has unexpected name" >&2
    exit 1
fi

STAGED_TYPEC="$(find "$STAGED_ROOT" -type f -name 'usb_typec_manager.ko' -print -quit)"
STAGED_PDIC="$(find "$STAGED_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"
ROOTFS_TYPEC="$(find "$ROOTFS_MODULE_ROOT" -type f -name 'usb_typec_manager.ko' -print -quit)"
ROOTFS_PDIC="$(find "$ROOTFS_MODULE_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"

python3 "$TYPEC_PATCHER" --module "$STAGED_TYPEC" --verify-patched >/dev/null
python3 "$TYPEC_PATCHER" --module "$ROOTFS_TYPEC" --verify-patched >/dev/null
for pdic in "$STAGED_PDIC" "$ROOTFS_PDIC"; do
    pdic_sha="$(sha256sum "$pdic" | awk '{print $1}')"
    if [[ "$pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
        echo "REFUSING U0e: PDIC module is not the restored original: $pdic" >&2
        exit 1
    fi
done

pmbootstrap chroot -r -- sh -ec "depmod -a '$KREL'; mkinitfs"

ROOTFS_INITRAMFS="$ROOTFS/boot/initramfs"
if [[ ! -f "$ROOTFS_INITRAMFS" ]]; then
    echo "REFUSING U0e: mkinitfs did not produce $ROOTFS_INITRAMFS" >&2
    exit 1
fi

rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"
INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    echo "REFUSING U0e: exported initramfs is missing" >&2
    exit 1
fi

extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
list_errors="$extract_dir/cpio-list.stderr"
extract_errors="$extract_dir/cpio-extract.stderr"
current_modules="$extract_dir/current-modules.txt"
trap 'rm -rf "$extract_dir"' EXIT

echo "=== Inspect exported U0e initramfs ==="
gzip -dc "$INITRAMFS" > "$cpio_archive"
if [[ ! -s "$cpio_archive" ]]; then
    echo "REFUSING U0e: decompressed initramfs is empty" >&2
    exit 1
fi
if ! cpio -it < "$cpio_archive" > "$entries" 2> "$list_errors"; then
    echo "REFUSING U0e: cpio could not list initramfs" >&2
    cat "$list_errors" >&2 || true
    exit 1
fi

python3 - "$entries" "$current_modules" <<'PY'
from pathlib import Path
import re
import sys

entries = Path(sys.argv[1]).read_text().splitlines()
modules: set[str] = set()
for entry in entries:
    name = Path(entry.strip()).name
    if not re.search(r"\.ko(?:\.(?:gz|xz|zst))?$", name):
        continue
    name = re.sub(r"\.(?:gz|xz|zst)$", "", name)
    name = re.sub(r"\.ko$", "", name)
    modules.add(name.replace("-", "_"))
Path(sys.argv[2]).write_text("\n".join(sorted(modules)) + ("\n" if modules else ""))
PY

module_count="$(python3 - "$PORT_ROOT/build/u0b-embedded-modules.txt" "$current_modules" <<'PY'
from pathlib import Path
import sys

baseline = {line.strip() for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()}
current = {line.strip() for line in Path(sys.argv[2]).read_text().splitlines() if line.strip()}
missing = baseline - current
added = current - baseline
if missing:
    raise SystemExit(f"U0e is missing U0b modules: {sorted(missing)}")
if added != {"i2c_dev"}:
    raise SystemExit(f"U0e module delta must be exactly i2c_dev, found: {sorted(added)}")
print(len(current))
PY
)"

if [[ "$module_count" != "$((BASE_MODULE_COUNT + 1))" ]]; then
    echo "REFUSING U0e: expected $((BASE_MODULE_COUNT + 1)) modules, found $module_count" >&2
    exit 1
fi

echo "U0e module delta from U0b: +i2c_dev ($module_count total)"

for entry in \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch.sh \
    usr/libexec/a33x-muic-switch
 do
    if ! grep -qx "$entry" "$entries"; then
        echo "REFUSING U0e: required initramfs entry is missing: $entry" >&2
        exit 1
    fi
 done

find_entry() {
    local suffix="$1"
    python3 - "$entries" "$suffix" <<'PY'
from pathlib import Path
import sys
matches = [line.strip() for line in Path(sys.argv[1]).read_text().splitlines()
           if line.strip().endswith(sys.argv[2])]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one entry ending with {sys.argv[2]!r}, found {len(matches)}")
print(matches[0])
PY
}

TYPEC_ENTRY="$(find_entry '/usb_typec_manager.ko')"
PDIC_ENTRY="$(find_entry '/pdic_notifier_module.ko')"
I2C_DEV_ENTRY="$(find_entry '/i2c-dev.ko')"
HELPER_ENTRY="$(find_entry '/usr/libexec/a33x-muic-switch')"
HOOK_ENTRY="$(find_entry '/hooks/03-a33x-muic-switch.sh')"

(
    cd "$extract_dir"
    if ! cpio -i --make-directories --quiet \
        "$TYPEC_ENTRY" "$PDIC_ENTRY" "$I2C_DEV_ENTRY" "$HELPER_ENTRY" "$HOOK_ENTRY" \
        < "$cpio_archive" 2> "$extract_errors"
    then
        echo "REFUSING U0e: failed to extract validation artifacts" >&2
        cat "$extract_errors" >&2 || true
        exit 1
    fi
)

EMBEDDED_TYPEC="$extract_dir/$TYPEC_ENTRY"
EMBEDDED_PDIC="$extract_dir/$PDIC_ENTRY"
EMBEDDED_I2C_DEV="$extract_dir/$I2C_DEV_ENTRY"
EMBEDDED_HELPER="$extract_dir/$HELPER_ENTRY"
EMBEDDED_HOOK="$extract_dir/$HOOK_ENTRY"

python3 "$TYPEC_PATCHER" --module "$EMBEDDED_TYPEC" --verify-patched >/dev/null
if ! cmp -s "$STAGED_TYPEC" "$EMBEDDED_TYPEC"; then
    echo "REFUSING U0e: embedded Type-C module differs from staged U0d patch" >&2
    exit 1
fi
if [[ "$(sha256sum "$EMBEDDED_PDIC" | awk '{print $1}')" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0e: embedded PDIC module is not original" >&2
    exit 1
fi
if [[ "$(modinfo -F name "$EMBEDDED_I2C_DEV")" != "i2c_dev" ]]; then
    echo "REFUSING U0e: embedded i2c-dev module metadata is wrong" >&2
    exit 1
fi
if ! cmp -s "$ROOTFS_HELPER" "$EMBEDDED_HELPER"; then
    echo "REFUSING U0e: embedded helper differs from installed package helper" >&2
    exit 1
fi
if ! cmp -s "$ROOTFS_HOOK" "$EMBEDDED_HOOK"; then
    echo "REFUSING U0e: embedded hook differs from installed package hook" >&2
    exit 1
fi

helper_sha="$(sha256sum "$EMBEDDED_HELPER" | awk '{print $1}')"
hook_sha="$(sha256sum "$EMBEDDED_HOOK" | awk '{print $1}')"
i2c_dev_sha="$(sha256sum "$EMBEDDED_I2C_DEV" | awk '{print $1}')"

{
    echo "helper=$EMBEDDED_HELPER"
    echo "helper_file=$(file -b "$EMBEDDED_HELPER")"
    echo "helper_sha256=$helper_sha"
    echo "hook_sha256=$hook_sha"
    echo "i2c_dev_sha256=$i2c_dev_sha"
    echo "bus=2"
    echo "address=0x3e"
    echo "sequence=0x6d:0x13,0x70:0x24,0x6d:0x17"
    echo "rollback=enabled"
    echo "owned_address_refusal=2-003e"
} | tee "$PORT_ROOT/build/u0e-muic-switch-helper.txt"

echo "=== Verify U0e initramfs safety and activation ==="
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-driver.sh" "$INITRAMFS"
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-hook.sh" "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-initramfs-safety.py" --initramfs "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-module-activation.py" \
    --contracts "$REPO_ROOT/config/module-activation-contracts.tsv" \
    --repo-root "$REPO_ROOT" \
    --initramfs "$INITRAMFS"

echo
echo "=== U0e initramfs ready ==="
echo "initramfs=$INITRAMFS"
echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
echo "patched_typec_sha256=$(sha256sum "$EMBEDDED_TYPEC" | awk '{print $1}')"
echo "original_pdic_sha256=$ORIGINAL_PDIC_SHA256"
echo "i2c_dev_sha256=$i2c_dev_sha"
echo "muic_helper_sha256=$helper_sha"
echo "muic_hook_sha256=$hook_sha"
echo "embedded_modules=$module_count"
echo "module_delta_from_u0b=+i2c_dev"

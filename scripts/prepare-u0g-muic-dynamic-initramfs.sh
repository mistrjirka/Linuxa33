#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
TYPEC_SHA256="${TYPEC_SHA256:-de92f9dc0d29d671bd20f42ad01688e0584eb8e43f6826ff2643e0767c814641}"
PDIC_SHA256="${PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"
I2C_DEV_SHA256="${I2C_DEV_SHA256:-7553147cc20782c1fd7f86cd7494166d7c97b44591cd7dd81a086f1be7a81654}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
U0E_PREP="$REPO_ROOT/scripts/run-prepare-u0e-muic-switch-initramfs.sh"
U0B_MODULES="$PORT_ROOT/build/u0b-embedded-modules.txt"
REPORT="$PORT_ROOT/build/u0g-muic-dynamic.txt"

SWITCH_PACKAGE="postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic"
PERSIST_PACKAGE="postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic"
SWITCH_SOURCE="$REPO_ROOT/pmaports/main/$SWITCH_PACKAGE"
PERSIST_SOURCE="$REPO_ROOT/pmaports/main/$PERSIST_PACKAGE"

for command in pmbootstrap sudo sha256sum gzip cpio cmp python3 file readelf modinfo; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$ROOTFS" \
    "$U0E_PREP" \
    "$U0B_MODULES" \
    "$SWITCH_SOURCE/APKBUILD" \
    "$SWITCH_SOURCE/a33x-muic-switch-dynamic.c" \
    "$SWITCH_SOURCE/03-a33x-muic-switch-dynamic.sh" \
    "$SWITCH_SOURCE/03-a33x-muic-switch-dynamic.files" \
    "$PERSIST_SOURCE/APKBUILD" \
    "$PERSIST_SOURCE/04-a33x-muic-persist-dynamic.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
SWITCH_DEST="$PMAPORTS/main/$SWITCH_PACKAGE"
PERSIST_DEST="$PMAPORTS/main/$PERSIST_PACKAGE"

ROOTFS_HELPER="$ROOTFS/usr/libexec/a33x-muic-switch-dynamic"
ROOTFS_HOOK03="$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch-dynamic.sh"
ROOTFS_HOOK04="$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist-dynamic.sh"

remove_package_if_present() {
    local package="$1"
    if pmbootstrap chroot -r -- apk info -e "$package" >/dev/null 2>&1; then
        echo "Removing stale experiment package: $package"
        pmbootstrap chroot -r -- apk del "$package"
    fi
}

echo "=== Recreate exact U0e 67-module functional base ==="
bash "$U0E_PREP"

# Replace only the old hardcoded-bus helper/observer packages. Keep the U0e
# module profile, Type-C patch, original PDIC, watchdog and USB-PD hooks.
for package in \
    postmarketos-mkinitfs-hook-a33x-muic-switch \
    postmarketos-mkinitfs-hook-a33x-muic-persist \
    "$SWITCH_PACKAGE" \
    "$PERSIST_PACKAGE"
do
    remove_package_if_present "$package"
done

for stale in \
    "$ROOTFS/usr/libexec/a33x-muic-switch" \
    "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch.sh" \
    "$ROOTFS/usr/share/mkinitfs/files/03-a33x-muic-switch.files" \
    "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh" \
    "$ROOTFS_HELPER" "$ROOTFS_HOOK03" "$ROOTFS_HOOK04"
do
    if [[ -e "$stale" ]]; then
        echo "REFUSING U0g: stale experiment file remained after package removal: $stale" >&2
        exit 1
    fi
done

echo "=== Build and install U0g dynamic packages ==="
rm -rf "$SWITCH_DEST" "$PERSIST_DEST"
mkdir -p "$(dirname "$SWITCH_DEST")"
cp -a "$SWITCH_SOURCE" "$SWITCH_DEST"
cp -a "$PERSIST_SOURCE" "$PERSIST_DEST"

pmbootstrap checksum "$SWITCH_PACKAGE"
pmbootstrap checksum "$PERSIST_PACKAGE"
pmbootstrap build --force "$SWITCH_PACKAGE"
pmbootstrap build --force "$PERSIST_PACKAGE"
pmbootstrap chroot -r --add "$SWITCH_PACKAGE,$PERSIST_PACKAGE" -- true

pmbootstrap chroot -r -- sh -ec '
apk info -e postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic
apk info -e postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic
test -x /usr/libexec/a33x-muic-switch-dynamic
test -x /usr/share/mkinitfs/hooks/03-a33x-muic-switch-dynamic.sh
test -x /usr/share/mkinitfs/hooks/04-a33x-muic-persist-dynamic.sh
'

for required in "$ROOTFS_HELPER" "$ROOTFS_HOOK03" "$ROOTFS_HOOK04"; do
    if [[ ! -x "$required" ]]; then
        echo "REFUSING U0g: installed artifact is missing or non-executable: $required" >&2
        exit 1
    fi
done

helper_file="$(file -b "$ROOTFS_HELPER")"
if [[ "$helper_file" != *"ELF 64-bit"* || "$helper_file" != *"ARM aarch64"* || "$helper_file" != *"statically linked"* ]]; then
    echo "REFUSING U0g: helper is not a static AArch64 ELF" >&2
    echo "$helper_file" >&2
    exit 1
fi
if ! readelf -hW "$ROOTFS_HELPER" | grep -q 'Machine:.*AArch64'; then
    echo "REFUSING U0g: helper ELF machine is not AArch64" >&2
    exit 1
fi

for required_text in \
    'controller="13860000.hsi2c"' \
    'discovery=physical-path' \
    'readlink -f "$entry"' \
    'selected_bus="${selected_entry##*-}"' \
    'selected_device="/dev/i2c-$selected_bus"' \
    'address_device="/sys/bus/i2c/devices/$selected_bus-$address_hex"' \
    'a33x-muic-switch-v2: success device='
do
    if ! grep -Fq "$required_text" "$ROOTFS_HOOK03" "$ROOTFS_HELPER"; then
        echo "REFUSING U0g: dynamic-selection contract text is missing: $required_text" >&2
        exit 1
    fi
done

if grep -Fq '/dev/i2c-2' "$ROOTFS_HOOK03"; then
    echo "REFUSING U0g: dynamic hook still hardcodes /dev/i2c-2" >&2
    exit 1
fi
if grep -Fq '2-003e' "$ROOTFS_HOOK03"; then
    echo "REFUSING U0g: dynamic hook still hardcodes bus-2 ownership" >&2
    exit 1
fi

for required_text in \
    'u0g-muic-result.txt' \
    '/run/a33x-muic-switch-selection.env' \
    '/run/a33x-muic-switch-helper.log' \
    'perform no I2C register access'
do
    if ! grep -Fq "$required_text" "$ROOTFS_HOOK04"; then
        echo "REFUSING U0g: persistence contract text is missing: $required_text" >&2
        exit 1
    fi
done
if grep -Eq 'I2C_(SLAVE|SMBUS)|0x6d|0x70' "$ROOTFS_HOOK04"; then
    echo "REFUSING U0g: persistence hook contains functional MUIC operations" >&2
    exit 1
fi

echo "=== Rebuild U0g initramfs ==="
pmbootstrap chroot -r -- sh -ec 'mkinitfs'

ROOTFS_INITRAMFS="$ROOTFS/boot/initramfs"
if [[ ! -f "$ROOTFS_INITRAMFS" ]]; then
    echo "REFUSING U0g: mkinitfs did not produce $ROOTFS_INITRAMFS" >&2
    exit 1
fi

rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"
INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    echo "REFUSING U0g: exported initramfs is missing" >&2
    exit 1
fi

extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
modules="$extract_dir/modules.txt"
trap 'rm -rf "$extract_dir"' EXIT

gzip -dc "$INITRAMFS" > "$cpio_archive"
cpio -it < "$cpio_archive" > "$entries" 2> "$extract_dir/cpio-list.stderr"

python3 - "$entries" "$modules" <<'PY'
from pathlib import Path
import re
import sys
entries = Path(sys.argv[1]).read_text().splitlines()
modules = set()
for entry in entries:
    name = Path(entry.strip()).name
    if not re.search(r"\.ko(?:\.(?:gz|xz|zst))?$", name):
        continue
    name = re.sub(r"\.(?:gz|xz|zst)$", "", name)
    name = re.sub(r"\.ko$", "", name)
    modules.add(name.replace("-", "_"))
Path(sys.argv[2]).write_text("\n".join(sorted(modules)) + "\n")
PY

module_count="$(python3 - "$U0B_MODULES" "$modules" <<'PY'
from pathlib import Path
import sys
baseline = {x.strip() for x in Path(sys.argv[1]).read_text().splitlines() if x.strip()}
current = {x.strip() for x in Path(sys.argv[2]).read_text().splitlines() if x.strip()}
missing = baseline - current
added = current - baseline
if missing:
    raise SystemExit(f"U0g is missing U0b modules: {sorted(missing)}")
if added != {"i2c_dev"}:
    raise SystemExit(f"U0g module delta must remain exactly i2c_dev, found: {sorted(added)}")
print(len(current))
PY
)"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0g: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

echo "U0g module delta from U0b remains: +i2c_dev ($module_count total)"

for entry in \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    usr/libexec/a33x-muic-switch-dynamic
do
    if ! grep -qx "$entry" "$entries"; then
        echo "REFUSING U0g: required initramfs entry is missing: $entry" >&2
        exit 1
    fi
done
for forbidden in \
    hooks/03-a33x-muic-switch.sh \
    hooks/04-a33x-muic-persist.sh \
    usr/libexec/a33x-muic-switch
do
    if grep -qx "$forbidden" "$entries"; then
        echo "REFUSING U0g: old hardcoded experiment artifact remains: $forbidden" >&2
        exit 1
    fi
done

find_module_entry() {
    local kind="$1"
    python3 - "$entries" "$kind" <<'PY'
from pathlib import Path
import re
import sys
entries = [x.strip() for x in Path(sys.argv[1]).read_text().splitlines()]
patterns = {
    "typec": r"/usb_typec_manager\.ko(?:\.(?:gz|xz|zst))?$",
    "pdic": r"/pdic_notifier_module\.ko(?:\.(?:gz|xz|zst))?$",
    "i2c": r"/(?:i2c-dev|i2c_dev)\.ko(?:\.(?:gz|xz|zst))?$",
}
matches = [x for x in entries if re.search(patterns[sys.argv[2]], x)]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one {sys.argv[2]} entry, found {matches}")
print(matches[0])
PY
}

TYPEC_ENTRY="$(find_module_entry typec)"
PDIC_ENTRY="$(find_module_entry pdic)"
I2C_ENTRY="$(find_module_entry i2c)"
HELPER_ENTRY=usr/libexec/a33x-muic-switch-dynamic
HOOK03_ENTRY=hooks/03-a33x-muic-switch-dynamic.sh
HOOK04_ENTRY=hooks/04-a33x-muic-persist-dynamic.sh

(
    cd "$extract_dir"
    cpio -i --make-directories --quiet \
        "$TYPEC_ENTRY" "$PDIC_ENTRY" "$I2C_ENTRY" \
        "$HELPER_ENTRY" "$HOOK03_ENTRY" "$HOOK04_ENTRY" \
        < "$cpio_archive" 2> "$extract_dir/cpio-extract.stderr"
)

EMBEDDED_TYPEC="$extract_dir/$TYPEC_ENTRY"
EMBEDDED_PDIC="$extract_dir/$PDIC_ENTRY"
EMBEDDED_I2C="$extract_dir/$I2C_ENTRY"
EMBEDDED_HELPER="$extract_dir/$HELPER_ENTRY"
EMBEDDED_HOOK03="$extract_dir/$HOOK03_ENTRY"
EMBEDDED_HOOK04="$extract_dir/$HOOK04_ENTRY"

verify_sha() {
    local label="$1" file="$2" expected="$3" actual
    actual="$(sha256sum "$file" | awk '{print $1}')"
    if [[ "$actual" != "$expected" ]]; then
        echo "REFUSING U0g: $label SHA256 mismatch: expected=$expected actual=$actual" >&2
        exit 1
    fi
}
verify_sha typec "$EMBEDDED_TYPEC" "$TYPEC_SHA256"
verify_sha pdic "$EMBEDDED_PDIC" "$PDIC_SHA256"
verify_sha i2c_dev "$EMBEDDED_I2C" "$I2C_DEV_SHA256"

if ! cmp -s "$ROOTFS_HELPER" "$EMBEDDED_HELPER"; then
    echo "REFUSING U0g: embedded helper differs from installed helper" >&2
    exit 1
fi
if ! cmp -s "$ROOTFS_HOOK03" "$EMBEDDED_HOOK03"; then
    echo "REFUSING U0g: embedded hook 03 differs from installed hook" >&2
    exit 1
fi
if ! cmp -s "$ROOTFS_HOOK04" "$EMBEDDED_HOOK04"; then
    echo "REFUSING U0g: embedded hook 04 differs from installed hook" >&2
    exit 1
fi
if [[ "$(modinfo -F name "$EMBEDDED_I2C")" != "i2c_dev" ]]; then
    echo "REFUSING U0g: embedded I2C module metadata is wrong" >&2
    exit 1
fi

{
    echo "candidate=U0g-muic-dynamic"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_base=U0f-muic-persist"
    echo "functional_delta=dynamic_runtime_bus_for_13860000_hsi2c"
    echo "muic_controller=13860000.hsi2c"
    echo "muic_address=0x3e"
    echo "muic_sequence=0x6d:0x13,0x70:0x24,0x6d:0x17"
    echo "muic_rollback=enabled_on_partial_failure"
    echo "runtime_bus_policy=discover_unique_i2c_dev_physical_path"
    echo "owned_address_policy=refuse_if_runtime_bus_003e_exists"
    echo "metadata_result=/a33x-bringup/u0g-muic-result.txt"
    echo "module_name_delta_from_u0b=+i2c_dev"
    echo "embedded_modules=$module_count"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
    echo "retained_u0d_typec_sha256=$TYPEC_SHA256"
    echo "retained_u0d_pdic_sha256=$PDIC_SHA256"
    echo "retained_u0e_i2c_dev_sha256=$I2C_DEV_SHA256"
    echo "dynamic_helper_sha256=$(sha256sum "$EMBEDDED_HELPER" | awk '{print $1}')"
    echo "dynamic_hook03_sha256=$(sha256sum "$EMBEDDED_HOOK03" | awk '{print $1}')"
    echo "dynamic_hook04_sha256=$(sha256sum "$EMBEDDED_HOOK04" | awk '{print $1}')"
} | tee "$REPORT"

echo
echo "=== U0g initramfs ready ==="
cat "$REPORT"

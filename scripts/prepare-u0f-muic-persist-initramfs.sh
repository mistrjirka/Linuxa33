#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PACKAGE="postmarketos-mkinitfs-hook-a33x-muic-persist"
PACKAGE_SOURCE="$REPO_ROOT/pmaports/main/$PACKAGE"
U0E_PREP="$REPO_ROOT/scripts/run-prepare-u0e-muic-switch-initramfs.sh"
U0E_REPORT="$PORT_ROOT/build/u0e-muic-switch-helper.txt"
U0B_MODULES="$PORT_ROOT/build/u0b-embedded-modules.txt"
REPORT="$PORT_ROOT/build/u0f-muic-persist.txt"

for command in pmbootstrap sudo sha256sum gzip cpio cmp python3 file readelf; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$ROOTFS" \
    "$U0E_PREP" \
    "$U0B_MODULES" \
    "$PACKAGE_SOURCE/APKBUILD" \
    "$PACKAGE_SOURCE/04-a33x-muic-persist.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
PACKAGE_DEST="$PMAPORTS/main/$PACKAGE"
ROOTFS_HOOK04="$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh"

# A previous U0f installation must not contaminate the recreated U0e base.
if pmbootstrap chroot -r -- apk info -e "$PACKAGE" >/dev/null 2>&1; then
    echo "=== Remove previous U0f observability overlay ==="
    pmbootstrap chroot -r -- apk del "$PACKAGE"
fi

if [[ -e "$ROOTFS_HOOK04" ]]; then
    echo "REFUSING U0f: persistence hook remained after package removal: $ROOTFS_HOOK04" >&2
    exit 1
fi

echo "=== Recreate exact U0e functional base ==="
bash "$U0E_PREP"

if [[ ! -f "$U0E_REPORT" ]]; then
    echo "REFUSING U0f: U0e preparation report is missing: $U0E_REPORT" >&2
    exit 1
fi

u0e_helper_sha="$(awk -F= '$1=="helper_sha256" {print $2}' "$U0E_REPORT")"
u0e_hook03_sha="$(awk -F= '$1=="hook_sha256" {print $2}' "$U0E_REPORT")"
u0e_i2c_dev_sha="$(awk -F= '$1=="i2c_dev_sha256" {print $2}' "$U0E_REPORT")"

for value_name in u0e_helper_sha u0e_hook03_sha u0e_i2c_dev_sha; do
    value="${!value_name}"
    if [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
        echo "REFUSING U0f: invalid or missing U0e report field: $value_name=$value" >&2
        exit 1
    fi
done

echo "=== Build and install U0f observability package ==="
rm -rf "$PACKAGE_DEST"
mkdir -p "$(dirname "$PACKAGE_DEST")"
cp -a "$PACKAGE_SOURCE" "$PACKAGE_DEST"
pmbootstrap checksum "$PACKAGE"
pmbootstrap build --force "$PACKAGE"
pmbootstrap chroot -r --add "$PACKAGE" -- true

pmbootstrap chroot -r -- sh -ec '
apk info -e postmarketos-mkinitfs-hook-a33x-muic-persist
test -x /usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh
'

if [[ ! -x "$ROOTFS_HOOK04" ]]; then
    echo "REFUSING U0f: installed persistence hook is missing or non-executable" >&2
    exit 1
fi

for required_text in \
    'functional_base=U0e-muic-switch' \
    'functional_delta=none' \
    'u0f-muic-result.txt' \
    '/dev/block/sda26' \
    '/run/a33x-muic-switch-helper.log' \
    'mount -t ext4 -o rw,nosuid,nodev,noatime' \
    'success metadata result synchronized and unmounted'
do
    if ! grep -Fq "$required_text" "$ROOTFS_HOOK04"; then
        echo "REFUSING U0f: persistence contract text is missing: $required_text" >&2
        exit 1
    fi
done

if grep -Eq 'I2C_(SLAVE|SMBUS)|0x6d|0x70|0x3e' "$ROOTFS_HOOK04"; then
    echo "REFUSING U0f: observability hook contains MUIC register/slave operations" >&2
    grep -En 'I2C_(SLAVE|SMBUS)|0x6d|0x70|0x3e' "$ROOTFS_HOOK04" >&2 || true
    exit 1
fi

echo "=== Rebuild U0f initramfs ==="
pmbootstrap chroot -r -- sh -ec 'mkinitfs'

ROOTFS_INITRAMFS="$ROOTFS/boot/initramfs"
if [[ ! -f "$ROOTFS_INITRAMFS" ]]; then
    echo "REFUSING U0f: mkinitfs did not produce $ROOTFS_INITRAMFS" >&2
    exit 1
fi

rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"
INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    echo "REFUSING U0f: exported initramfs is missing" >&2
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
    raise SystemExit(f"U0f is missing U0b modules: {sorted(missing)}")
if added != {"i2c_dev"}:
    raise SystemExit(f"U0f module delta must remain exactly i2c_dev, found: {sorted(added)}")
print(len(current))
PY
)"

if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0f: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

echo "U0f module delta from U0b remains: +i2c_dev ($module_count total)"

for entry in \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch.sh \
    hooks/04-a33x-muic-persist.sh \
    usr/libexec/a33x-muic-switch
do
    if ! grep -qx "$entry" "$entries"; then
        echo "REFUSING U0f: required initramfs entry is missing: $entry" >&2
        exit 1
    fi
done

find_entry() {
    local kind="$1"
    python3 - "$entries" "$kind" <<'PY'
from pathlib import Path
import re
import sys

entries = [x.strip() for x in Path(sys.argv[1]).read_text().splitlines()]
kind = sys.argv[2]
if kind == "i2c_dev":
    matches = [x for x in entries if re.search(r"/(?:i2c-dev|i2c_dev)\.ko(?:\.(?:gz|xz|zst))?$", x)]
else:
    matches = [x for x in entries if x == kind]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one {kind!r} entry, found {matches}")
print(matches[0])
PY
}

HELPER_ENTRY="$(find_entry usr/libexec/a33x-muic-switch)"
HOOK03_ENTRY="$(find_entry hooks/03-a33x-muic-switch.sh)"
HOOK04_ENTRY="$(find_entry hooks/04-a33x-muic-persist.sh)"
I2C_ENTRY="$(find_entry i2c_dev)"

(
    cd "$extract_dir"
    cpio -i --make-directories --quiet \
        "$HELPER_ENTRY" "$HOOK03_ENTRY" "$HOOK04_ENTRY" "$I2C_ENTRY" \
        < "$cpio_archive" 2> "$extract_dir/cpio-extract.stderr"
)

EMBEDDED_HELPER="$extract_dir/$HELPER_ENTRY"
EMBEDDED_HOOK03="$extract_dir/$HOOK03_ENTRY"
EMBEDDED_HOOK04="$extract_dir/$HOOK04_ENTRY"
EMBEDDED_I2C="$extract_dir/$I2C_ENTRY"

helper_sha="$(sha256sum "$EMBEDDED_HELPER" | awk '{print $1}')"
hook03_sha="$(sha256sum "$EMBEDDED_HOOK03" | awk '{print $1}')"
hook04_sha="$(sha256sum "$EMBEDDED_HOOK04" | awk '{print $1}')"
i2c_sha="$(sha256sum "$EMBEDDED_I2C" | awk '{print $1}')"
installed_hook04_sha="$(sha256sum "$ROOTFS_HOOK04" | awk '{print $1}')"

if [[ "$helper_sha" != "$u0e_helper_sha" ]]; then
    echo "REFUSING U0f: helper differs from exact U0e helper" >&2
    exit 1
fi
if [[ "$hook03_sha" != "$u0e_hook03_sha" ]]; then
    echo "REFUSING U0f: functional hook 03 differs from exact U0e hook" >&2
    exit 1
fi
if [[ "$i2c_sha" != "$u0e_i2c_dev_sha" ]]; then
    echo "REFUSING U0f: i2c_dev differs from exact U0e module" >&2
    exit 1
fi
if [[ "$hook04_sha" != "$installed_hook04_sha" ]]; then
    echo "REFUSING U0f: embedded persistence hook differs from installed hook" >&2
    exit 1
fi

helper_file="$(file -b "$EMBEDDED_HELPER")"
if [[ "$helper_file" != *"ELF 64-bit"* || "$helper_file" != *"ARM aarch64"* || "$helper_file" != *"statically linked"* ]]; then
    echo "REFUSING U0f: retained helper is not a static AArch64 ELF" >&2
    exit 1
fi
if ! readelf -hW "$EMBEDDED_HELPER" | grep -q 'Machine:.*AArch64'; then
    echo "REFUSING U0f: retained helper ELF machine is not AArch64" >&2
    exit 1
fi

{
    echo "candidate=U0f-muic-persist"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_base=U0e-muic-switch"
    echo "functional_delta=none"
    echo "observability_delta=hook04_metadata_persistence"
    echo "metadata_partition=/dev/block/sda26"
    echo "metadata_result=/a33x-bringup/u0f-muic-result.txt"
    echo "module_name_delta_from_u0b=+i2c_dev"
    echo "embedded_modules=$module_count"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
    echo "retained_u0e_helper_sha256=$helper_sha"
    echo "retained_u0e_hook03_sha256=$hook03_sha"
    echo "retained_u0e_i2c_dev_sha256=$i2c_sha"
    echo "persistence_hook04_sha256=$hook04_sha"
    echo "u0e_report=$U0E_REPORT"
} | tee "$REPORT"

echo
echo "=== U0f initramfs ready ==="
cat "$REPORT"

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ROOT="${ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
ORIGINAL_PDIC_SHA256="${ORIGINAL_PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INITRAMFS="$ROOT/export-debug/initramfs"
MODULE_ROOT="$ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
U0B_MODULES="$ROOT/build/u0b-embedded-modules.txt"
TYPEC_PATCHER="$REPO_ROOT/scripts/patch-typec-muic-none-mask.py"
PDIC_PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"
HELPER_REPORT="$ROOT/build/u0e-muic-switch-helper.txt"
OUT="$ROOT/build/pmos-debug-recovery-u0e"
CANDIDATE="$ROOT/build/candidates/a33x-h1-usbpd-u0e-muic-switch-recovery.img"
MANIFEST="$ROOT/build/candidates/a33x-h1-usbpd-u0e-muic-switch-manifest.txt"
TYPEC_PATCH_REPORT="$ROOT/build/u0d-typec-muic-none-patch.txt"

for command in gzip cpio sha256sum modinfo cmp python3 file readelf; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$INITRAMFS" \
    "$MODULE_ROOT/modules.dep" \
    "$U0B_MODULES" \
    "$TYPEC_PATCHER" \
    "$PDIC_PATCHER" \
    "$HELPER_REPORT" \
    "$TYPEC_PATCH_REPORT" \
    "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

STAGED_TYPEC="$(find "$MODULE_ROOT" -type f -name 'usb_typec_manager.ko' -print -quit)"
STAGED_PDIC="$(find "$MODULE_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"
STAGED_I2C_DEV="$(find "$MODULE_ROOT" -type f \( -name 'i2c-dev.ko' -o -name 'i2c_dev.ko' \) -print -quit)"

for required in "$STAGED_TYPEC" "$STAGED_PDIC" "$STAGED_I2C_DEV"; do
    if [[ -z "$required" || ! -f "$required" ]]; then
        echo "Missing staged module: $required" >&2
        exit 1
    fi
done

python3 "$TYPEC_PATCHER" --module "$STAGED_TYPEC" --verify-patched >/dev/null
staged_pdic_sha="$(sha256sum "$STAGED_PDIC" | awk '{print $1}')"
if [[ "$staged_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0e: staged PDIC module is not the original binary" >&2
    exit 1
fi
if python3 "$PDIC_PATCHER" --module "$STAGED_PDIC" --verify-patched >/dev/null 2>&1; then
    echo "REFUSING U0e: staged PDIC still contains the U0c factory patch" >&2
    exit 1
fi
if [[ "$(modinfo -F name "$STAGED_I2C_DEV")" != "i2c_dev" ]]; then
    echo "REFUSING U0e: staged I2C character module has unexpected metadata" >&2
    exit 1
fi

extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
list_errors="$extract_dir/cpio-list.stderr"
extract_errors="$extract_dir/cpio-extract.stderr"
current_modules="$extract_dir/current-modules.txt"
trap 'rm -rf "$extract_dir"' EXIT

echo "=== Validate U0e initramfs payload ==="
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

module_count="$(python3 - "$U0B_MODULES" "$current_modules" <<'PY'
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
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0e: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

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
HELPER_ENTRY="usr/libexec/a33x-muic-switch"
HOOK_ENTRY="hooks/03-a33x-muic-switch.sh"

(
    cd "$extract_dir"
    if ! cpio -i --make-directories --quiet \
        "$TYPEC_ENTRY" "$PDIC_ENTRY" "$I2C_DEV_ENTRY" "$HELPER_ENTRY" "$HOOK_ENTRY" \
        < "$cpio_archive" 2> "$extract_errors"
    then
        echo "REFUSING U0e: failed to extract embedded validation artifacts" >&2
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
embedded_pdic_sha="$(sha256sum "$EMBEDDED_PDIC" | awk '{print $1}')"
if [[ "$embedded_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0e: embedded PDIC is not the restored original" >&2
    exit 1
fi
if [[ "$(modinfo -F name "$EMBEDDED_I2C_DEV")" != "i2c_dev" ]]; then
    echo "REFUSING U0e: embedded i2c-dev module metadata is wrong" >&2
    exit 1
fi

helper_file="$(file -b "$EMBEDDED_HELPER")"
if [[ "$helper_file" != *"ELF 64-bit"* || "$helper_file" != *"ARM aarch64"* || "$helper_file" != *"statically linked"* ]]; then
    echo "REFUSING U0e: embedded helper is not a static AArch64 ELF" >&2
    echo "$helper_file" >&2
    exit 1
fi
if ! readelf -hW "$EMBEDDED_HELPER" | grep -q 'Machine:.*AArch64'; then
    echo "REFUSING U0e: embedded helper ELF machine is not AArch64" >&2
    exit 1
fi

for required_text in \
    'bus=2 address=0x3e' \
    'expected-adapter=13860000.hsi2c' \
    '/sys/bus/i2c/devices/2-003e' \
    'success bus=2 address=0x3e ctrl1=0x17 switch=0x24'
do
    if ! grep -Fq "$required_text" "$EMBEDDED_HOOK"; then
        echo "REFUSING U0e: embedded hook contract is missing: $required_text" >&2
        exit 1
    fi
done

initramfs_sha256="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
typec_sha256="$(sha256sum "$EMBEDDED_TYPEC" | awk '{print $1}')"
i2c_dev_sha256="$(sha256sum "$EMBEDDED_I2C_DEV" | awk '{print $1}')"
helper_sha256="$(sha256sum "$EMBEDDED_HELPER" | awk '{print $1}')"
hook_sha256="$(sha256sum "$EMBEDDED_HOOK" | awk '{print $1}')"

report_helper_sha="$(awk -F= '$1=="helper_sha256" {print $2}' "$HELPER_REPORT")"
report_i2c_sha="$(awk -F= '$1=="i2c_dev_sha256" {print $2}' "$HELPER_REPORT")"
report_hook_sha="$(awk -F= '$1=="hook_sha256" {print $2}' "$HELPER_REPORT")"
if [[ "$report_helper_sha" != "$helper_sha256" \
    || "$report_i2c_sha" != "$i2c_dev_sha256" \
    || "$report_hook_sha" != "$hook_sha256" ]]
then
    echo "REFUSING U0e: embedded helper artifacts differ from preparation report" >&2
    exit 1
fi

echo "=== U0e isolated delta ==="
echo "Embedded modules: $module_count (U0b/U0d set plus i2c_dev only)"
echo "Retained patch:   usb_typec_manager mask 0x16 -> 0x17"
echo "Restored module:  pdic_notifier_module (factory patch absent)"
echo "Added support:    i2c_dev"
echo "Added helper:     static AArch64 a33x-muic-switch"
echo "MUIC operation:   bus 2 address 0x3e; 6d=13, 70=24, 6d=17"
echo "Full MUIC/CPIF/BTS stack remains absent."
echo "No kernel command-line delta."

env \
    ROOT="$ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="" \
    bash "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"

SOURCE_IMAGE="$OUT/recovery.img"
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$SOURCE_IMAGE" "$CANDIDATE"

if [[ "$(stat -Lc '%s' "$CANDIDATE")" != "100663296" ]]; then
    echo "REFUSING U0e: unexpected recovery image size" >&2
    exit 1
fi
if grep -F -- 'pdic_notifier_module.f_usb_mode=' "$OUT/final-boot-info.txt" >/dev/null; then
    echo "REFUSING U0e: obsolete factory module parameter is present" >&2
    exit 1
fi

{
    echo "candidate=U0e-muic-switch"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_delta=s2mu106_muic_usb_data_switch_sequence"
    echo "retained_typec_delta=mask_0x16_to_0x17"
    echo "pdic_factory_patch=absent"
    echo "muic_bus=2"
    echo "muic_address=0x3e"
    echo "muic_sequence=0x6d:0x13,0x70:0x24,0x6d:0x17"
    echo "muic_rollback=enabled_on_partial_failure"
    echo "owned_address_policy=refuse_if_2-003e_exists"
    echo "kernel_cmdline_delta=none"
    echo "module_name_delta_from_u0b=+i2c_dev"
    echo "embedded_modules=$module_count"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$initramfs_sha256"
    echo "patched_typec_sha256=$typec_sha256"
    echo "restored_pdic_sha256=$embedded_pdic_sha"
    echo "i2c_dev_sha256=$i2c_dev_sha256"
    echo "muic_helper_sha256=$helper_sha256"
    echo "muic_hook_sha256=$hook_sha256"
    echo "typec_patch_report=$TYPEC_PATCH_REPORT"
    echo "helper_report=$HELPER_REPORT"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$(stat -Lc '%s' "$CANDIDATE")"
    echo "recovery_sha256=$(sha256sum "$CANDIDATE" | awk '{print $1}')"
} | tee "$MANIFEST"

echo
echo "U0e candidate: $CANDIDATE"
echo "Manifest:      $MANIFEST"

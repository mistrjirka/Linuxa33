#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ROOT="${ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-66}"
ORIGINAL_PDIC_SHA256="${ORIGINAL_PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INITRAMFS="$ROOT/export-debug/initramfs"
MODULE_ROOT="$ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
U0B_MODULES="$ROOT/build/u0b-embedded-modules.txt"
TYPEC_PATCHER="$REPO_ROOT/scripts/patch-typec-muic-none-mask.py"
PDIC_PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"
OUT="$ROOT/build/pmos-debug-recovery-u0d"
CANDIDATE="$ROOT/build/candidates/a33x-h1-usbpd-u0d-typec-muic-none-recovery.img"
MANIFEST="$ROOT/build/candidates/a33x-h1-usbpd-u0d-typec-muic-none-manifest.txt"
PATCH_REPORT="$ROOT/build/u0d-typec-muic-none-patch.txt"

for command in gzip cpio sha256sum modinfo cmp python3; do
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
    "$PATCH_REPORT" \
    "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

STAGED_TYPEC="$(find "$MODULE_ROOT" -type f -name 'usb_typec_manager.ko' -print -quit)"
STAGED_PDIC="$(find "$MODULE_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"

for required in "$STAGED_TYPEC" "$STAGED_PDIC"; do
    if [[ -z "$required" || ! -f "$required" ]]; then
        echo "Missing staged module: $required" >&2
        exit 1
    fi
done

python3 "$TYPEC_PATCHER" --module "$STAGED_TYPEC" --verify-patched >/dev/null
staged_pdic_sha="$(sha256sum "$STAGED_PDIC" | awk '{print $1}')"
if [[ "$staged_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0d: staged PDIC module is not the original binary" >&2
    exit 1
fi
if python3 "$PDIC_PATCHER" --module "$STAGED_PDIC" --verify-patched >/dev/null 2>&1; then
    echo "REFUSING U0d: staged PDIC still contains the U0c factory patch" >&2
    exit 1
fi

extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
list_errors="$extract_dir/cpio-list.stderr"
extract_errors="$extract_dir/cpio-extract.stderr"
current_modules="$extract_dir/current-modules.txt"
trap 'rm -rf "$extract_dir"' EXIT

echo "=== Validate U0d initramfs payload ==="
gzip -dc "$INITRAMFS" > "$cpio_archive"
if [[ ! -s "$cpio_archive" ]]; then
    echo "REFUSING U0d: decompressed initramfs is empty" >&2
    exit 1
fi
if ! cpio -it < "$cpio_archive" > "$entries" 2> "$list_errors"; then
    echo "REFUSING U0d: cpio could not list initramfs" >&2
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

python3 - "$U0B_MODULES" "$current_modules" "$EXPECTED_MODULE_COUNT" <<'PY'
from pathlib import Path
import sys

baseline = {line.strip() for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()}
current = {line.strip() for line in Path(sys.argv[2]).read_text().splitlines() if line.strip()}
expected = int(sys.argv[3])
if len(current) != expected:
    raise SystemExit(f"U0d module count changed: expected {expected}, found {len(current)}")
if current != baseline:
    raise SystemExit(
        "U0d module set differs from U0b: "
        f"missing={sorted(baseline-current)}, added={sorted(current-baseline)}"
    )
print(f"U0d module set matches U0b: {len(current)} modules")
PY

if ! grep -q '^hooks/02-a33x-usbpd-load\.sh$' "$entries"; then
    echo "REFUSING U0d: U0b USB-PD loader hook is absent" >&2
    exit 1
fi
if ! grep -q '^hooks/01-a33x-watchdog\.sh$' "$entries"; then
    echo "REFUSING U0d: watchdog hook is absent" >&2
    exit 1
fi

find_entry() {
    local suffix="$1"
    python3 - "$entries" "$suffix" <<'PY'
from pathlib import Path
import sys
matches = [
    line.strip()
    for line in Path(sys.argv[1]).read_text().splitlines()
    if line.strip().endswith(sys.argv[2])
]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one entry ending with {sys.argv[2]!r}, found {len(matches)}")
print(matches[0])
PY
}

TYPEC_ENTRY="$(find_entry '/usb_typec_manager.ko')"
PDIC_ENTRY="$(find_entry '/pdic_notifier_module.ko')"

(
    cd "$extract_dir"
    if ! cpio -i --make-directories --quiet "$TYPEC_ENTRY" "$PDIC_ENTRY" \
        < "$cpio_archive" 2> "$extract_errors"
    then
        echo "REFUSING U0d: failed to extract embedded modules" >&2
        cat "$extract_errors" >&2 || true
        exit 1
    fi
)

EMBEDDED_TYPEC="$extract_dir/$TYPEC_ENTRY"
EMBEDDED_PDIC="$extract_dir/$PDIC_ENTRY"

python3 "$TYPEC_PATCHER" --module "$EMBEDDED_TYPEC" --verify-patched >/dev/null
if ! cmp -s "$STAGED_TYPEC" "$EMBEDDED_TYPEC"; then
    echo "REFUSING U0d: embedded Type-C module differs from staged patch" >&2
    exit 1
fi

embedded_pdic_sha="$(sha256sum "$EMBEDDED_PDIC" | awk '{print $1}')"
if [[ "$embedded_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0d: embedded PDIC is not restored to the original binary" >&2
    exit 1
fi
if ! cmp -s "$STAGED_PDIC" "$EMBEDDED_PDIC"; then
    echo "REFUSING U0d: embedded PDIC differs from staged original" >&2
    exit 1
fi

initramfs_sha256="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
typec_sha256="$(sha256sum "$EMBEDDED_TYPEC" | awk '{print $1}')"

echo "=== U0d isolated delta ==="
echo "Embedded modules: $EXPECTED_MODULE_COUNT (same set as U0b)"
echo "Patched module:   usb_typec_manager"
echo "Patched behavior: accepted cable-type mask 0x16 -> 0x17"
echo "Restored module:  pdic_notifier_module (U0c patch removed)"
echo "No kernel command-line delta."
echo "No MUIC/CPIF/BTS modules added."

env \
    ROOT="$ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="" \
    bash "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"

SOURCE_IMAGE="$OUT/recovery.img"
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$SOURCE_IMAGE" "$CANDIDATE"

if [[ "$(stat -Lc '%s' "$CANDIDATE")" != "100663296" ]]; then
    echo "REFUSING U0d: unexpected recovery image size" >&2
    exit 1
fi

if grep -F -- 'pdic_notifier_module.f_usb_mode=' "$OUT/final-boot-info.txt" >/dev/null; then
    echo "REFUSING U0d: obsolete factory module parameter is present" >&2
    exit 1
fi

{
    echo "candidate=U0d-typec-muic-none"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_delta=usb_typec_manager_accept_muic_none_for_real_ufp"
    echo "typec_mask_delta=0x16_to_0x17"
    echo "pdic_factory_patch=removed"
    echo "kernel_cmdline_delta=none"
    echo "module_name_delta_from_u0b=none"
    echo "embedded_modules=$EXPECTED_MODULE_COUNT"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$initramfs_sha256"
    echo "patched_module=usb_typec_manager"
    echo "patched_module_sha256=$typec_sha256"
    echo "restored_pdic_sha256=$embedded_pdic_sha"
    echo "patch_report=$PATCH_REPORT"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$(stat -Lc '%s' "$CANDIDATE")"
    echo "recovery_sha256=$(sha256sum "$CANDIDATE" | awk '{print $1}')"
} | tee "$MANIFEST"

echo
echo "U0d candidate: $CANDIDATE"
echo "Manifest:      $MANIFEST"

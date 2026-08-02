#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ROOT="${ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-66}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INITRAMFS="$ROOT/export-debug/initramfs"
MODULE_ROOT="$ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
U0B_MODULES="$ROOT/build/u0b-embedded-modules.txt"
PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"
OUT="$ROOT/build/pmos-debug-recovery-u0c"
CANDIDATE="$ROOT/build/candidates/a33x-h1-usbpd-u0c-pdic-factory-recovery.img"
MANIFEST="$ROOT/build/candidates/a33x-h1-usbpd-u0c-pdic-factory-manifest.txt"

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
    "$PATCHER" \
    "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

staged_pdic="$(
    find "$MODULE_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit
)"
if [[ -z "$staged_pdic" || ! -f "$staged_pdic" ]]; then
    echo "REFUSING U0c: staged pdic_notifier_module.ko was not found" >&2
    exit 1
fi
python3 "$PATCHER" --module "$staged_pdic" --verify-patched >/dev/null

current_modules="$(mktemp)"
extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
list_errors="$extract_dir/cpio-list.stderr"
extract_errors="$extract_dir/cpio-extract.stderr"
trap 'rm -f "$current_modules"; rm -rf "$extract_dir"' EXIT

echo "=== Validate U0c initramfs payload ==="
if ! gzip -dc "$INITRAMFS" > "$cpio_archive"; then
    echo "REFUSING U0c: failed to decompress initramfs" >&2
    exit 1
fi
if [[ ! -s "$cpio_archive" ]]; then
    echo "REFUSING U0c: decompressed initramfs is empty" >&2
    exit 1
fi
if ! cpio -it < "$cpio_archive" > "$entries" 2> "$list_errors"; then
    echo "REFUSING U0c: cpio could not list initramfs" >&2
    cat "$list_errors" >&2 || true
    exit 1
fi

python3 - "$entries" "$current_modules" <<'PY'
from pathlib import Path
import re
import sys

entries = Path(sys.argv[1]).read_text().splitlines()
output = Path(sys.argv[2])
pattern = re.compile(r"\.ko(?:\.(?:gz|xz|zst))?$")
modules: set[str] = set()
for entry in entries:
    name = Path(entry.strip()).name
    if not pattern.search(name):
        continue
    name = re.sub(r"\.(?:gz|xz|zst)$", "", name)
    name = re.sub(r"\.ko$", "", name)
    modules.add(name.replace("-", "_"))
output.write_text("\n".join(sorted(modules)) + ("\n" if modules else ""))
PY

module_count="$(wc -l < "$current_modules")"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0c: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi
if ! cmp -s "$U0B_MODULES" "$current_modules"; then
    echo "REFUSING U0c: embedded module set differs from U0b" >&2
    diff -u "$U0B_MODULES" "$current_modules" >&2 || true
    exit 1
fi

if ! grep -q '^hooks/02-a33x-usbpd-load\.sh$' "$entries"; then
    echo "REFUSING U0c: U0b USB-PD loader hook is absent" >&2
    exit 1
fi

pdic_entry="$(python3 - "$entries" <<'PY'
from pathlib import Path
import sys
matches = [
    line.strip()
    for line in Path(sys.argv[1]).read_text().splitlines()
    if line.strip().endswith("/pdic_notifier_module.ko")
]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one embedded pdic_notifier_module.ko, found {len(matches)}")
print(matches[0])
PY
)"

(
    cd "$extract_dir"
    if ! cpio -i --quiet "$pdic_entry" < "$cpio_archive" 2> "$extract_errors"; then
        echo "REFUSING U0c: failed to extract $pdic_entry" >&2
        cat "$extract_errors" >&2 || true
        exit 1
    fi
)
embedded_pdic="$extract_dir/$pdic_entry"
if [[ ! -f "$embedded_pdic" ]]; then
    echo "REFUSING U0c: failed to extract embedded PDIC module" >&2
    exit 1
fi

python3 "$PATCHER" --module "$embedded_pdic" --verify-patched >/dev/null
if ! cmp -s "$staged_pdic" "$embedded_pdic"; then
    echo "REFUSING U0c: embedded PDIC module differs from staged patched module" >&2
    exit 1
fi

initramfs_sha256="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
pdic_sha256="$(sha256sum "$embedded_pdic" | awk '{print $1}')"

echo "=== U0c isolated delta ==="
echo "Embedded modules: $module_count (same names as U0b)"
echo "Patched module:   pdic_notifier_module"
echo "Patched behavior: check_factory_mode_boot returns 1"
echo "No kernel command-line delta."
echo "No MUIC/CPIF/BTS modules added."

env \
    ROOT="$ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="" \
    bash "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"

source_image="$OUT/recovery.img"
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$source_image" "$CANDIDATE"

if [[ "$(stat -Lc '%s' "$CANDIDATE")" != "100663296" ]]; then
    echo "REFUSING U0c: unexpected recovery image size" >&2
    exit 1
fi

if grep -F -- 'pdic_notifier_module.f_usb_mode=' \
    "$OUT/final-boot-info.txt" >/dev/null
then
    echo "REFUSING U0c: obsolete factory module parameter is present" >&2
    exit 1
fi

{
    echo "candidate=U0c-pdic-factory"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_delta=patched_check_factory_mode_boot_return_1"
    echo "kernel_cmdline_delta=none"
    echo "module_name_delta_from_u0b=none"
    echo "embedded_modules=$module_count"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$initramfs_sha256"
    echo "patched_module=pdic_notifier_module"
    echo "patched_module_sha256=$pdic_sha256"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$(stat -Lc '%s' "$CANDIDATE")"
    echo "recovery_sha256=$(sha256sum "$CANDIDATE" | awk '{print $1}')"
} | tee "$MANIFEST"

echo
echo "U0c candidate: $CANDIDATE"
echo "Manifest:      $MANIFEST"

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ROOT="${ROOT:-$HOME/a33-port}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INITRAMFS="$ROOT/export-debug/initramfs"
U0B_MODULES="$ROOT/build/u0b-embedded-modules.txt"
REPORT="$ROOT/build/u0g-muic-dynamic.txt"
OUT="$ROOT/build/pmos-debug-recovery-u0g"
CANDIDATE="$ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-recovery.img"
MANIFEST="$ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-manifest.txt"

for command in gzip cpio sha256sum modinfo python3 file readelf; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$INITRAMFS" "$U0B_MODULES" "$REPORT" \
    "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

report_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key) + 2); exit}' "$REPORT"
}

expected_initramfs_sha="$(report_value initramfs_sha256)"
expected_typec_sha="$(report_value retained_u0d_typec_sha256)"
expected_pdic_sha="$(report_value retained_u0d_pdic_sha256)"
expected_i2c_sha="$(report_value retained_u0e_i2c_dev_sha256)"
expected_helper_sha="$(report_value dynamic_helper_sha256)"
expected_hook03_sha="$(report_value dynamic_hook03_sha256)"
expected_hook04_sha="$(report_value dynamic_hook04_sha256)"

for item in \
    "initramfs:$expected_initramfs_sha" \
    "typec:$expected_typec_sha" \
    "pdic:$expected_pdic_sha" \
    "i2c:$expected_i2c_sha" \
    "helper:$expected_helper_sha" \
    "hook03:$expected_hook03_sha" \
    "hook04:$expected_hook04_sha"
do
    name="${item%%:*}"
    value="${item#*:}"
    if [[ ! "$value" =~ ^[0-9a-f]{64}$ ]]; then
        echo "REFUSING U0g: invalid report hash: $name=$value" >&2
        exit 1
    fi
done

actual_initramfs_sha="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
if [[ "$actual_initramfs_sha" != "$expected_initramfs_sha" ]]; then
    echo "REFUSING U0g: initramfs differs from preparation report" >&2
    exit 1
fi

extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
modules="$extract_dir/modules.txt"
trap 'rm -rf "$extract_dir"' EXIT

echo "=== Validate U0g initramfs payload ==="
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

for entry in \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    usr/libexec/a33x-muic-switch-dynamic
do
    grep -qx "$entry" "$entries" || {
        echo "REFUSING U0g: required initramfs entry is missing: $entry" >&2
        exit 1
    }
done

find_entry() {
    local kind="$1"
    python3 - "$entries" "$kind" <<'PY'
from pathlib import Path
import re
import sys
entries = [x.strip() for x in Path(sys.argv[1]).read_text().splitlines()]
kind = sys.argv[2]
patterns = {
    "typec": r"/usb_typec_manager\.ko(?:\.(?:gz|xz|zst))?$",
    "pdic": r"/pdic_notifier_module\.ko(?:\.(?:gz|xz|zst))?$",
    "i2c": r"/(?:i2c-dev|i2c_dev)\.ko(?:\.(?:gz|xz|zst))?$",
}
if kind in patterns:
    matches = [x for x in entries if re.search(patterns[kind], x)]
else:
    matches = [x for x in entries if x == kind]
if len(matches) != 1:
    raise SystemExit(f"expected exactly one {kind!r} entry, found {matches}")
print(matches[0])
PY
}

TYPEC_ENTRY="$(find_entry typec)"
PDIC_ENTRY="$(find_entry pdic)"
I2C_ENTRY="$(find_entry i2c)"
HELPER_ENTRY="$(find_entry usr/libexec/a33x-muic-switch-dynamic)"
HOOK03_ENTRY="$(find_entry hooks/03-a33x-muic-switch-dynamic.sh)"
HOOK04_ENTRY="$(find_entry hooks/04-a33x-muic-persist-dynamic.sh)"

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
verify_sha typec "$EMBEDDED_TYPEC" "$expected_typec_sha"
verify_sha pdic "$EMBEDDED_PDIC" "$expected_pdic_sha"
verify_sha i2c_dev "$EMBEDDED_I2C" "$expected_i2c_sha"
verify_sha helper "$EMBEDDED_HELPER" "$expected_helper_sha"
verify_sha hook03 "$EMBEDDED_HOOK03" "$expected_hook03_sha"
verify_sha hook04 "$EMBEDDED_HOOK04" "$expected_hook04_sha"

if [[ "$(modinfo -F name "$EMBEDDED_I2C")" != "i2c_dev" ]]; then
    echo "REFUSING U0g: embedded I2C module metadata is wrong" >&2
    exit 1
fi
helper_file="$(file -b "$EMBEDDED_HELPER")"
if [[ "$helper_file" != *"ELF 64-bit"* || "$helper_file" != *"ARM aarch64"* || "$helper_file" != *"statically linked"* ]]; then
    echo "REFUSING U0g: helper is not a static AArch64 ELF" >&2
    exit 1
fi
if ! readelf -hW "$EMBEDDED_HELPER" | grep -q 'Machine:.*AArch64'; then
    echo "REFUSING U0g: helper ELF machine is not AArch64" >&2
    exit 1
fi

if grep -Fq '/dev/i2c-2' "$EMBEDDED_HOOK03"; then
    echo "REFUSING U0g: embedded hook still hardcodes /dev/i2c-2" >&2
    exit 1
fi
if ! grep -Fq 'controller="13860000.hsi2c"' "$EMBEDDED_HOOK03"; then
    echo "REFUSING U0g: embedded hook lacks physical-controller contract" >&2
    exit 1
fi
if grep -Eq 'I2C_(SLAVE|SMBUS)|0x6d|0x70' "$EMBEDDED_HOOK04"; then
    echo "REFUSING U0g: persistence hook contains functional MUIC operations" >&2
    exit 1
fi

echo "=== U0g isolated delta ==="
echo "Embedded modules: $module_count (unchanged from U0e/U0f)"
echo "Functional change: runtime bus discovery for physical 13860000.hsi2c"
echo "Register sequence: unchanged"
echo "Persistence: u0g metadata transcript"
echo "Kernel cmdline: unchanged"

env \
    ROOT="$ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="" \
    bash "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"

SOURCE_IMAGE="$OUT/recovery.img"
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$SOURCE_IMAGE" "$CANDIDATE"

if [[ "$(stat -Lc '%s' "$CANDIDATE")" != 100663296 ]]; then
    echo "REFUSING U0g: unexpected recovery image size" >&2
    exit 1
fi
if grep -F -- 'pdic_notifier_module.f_usb_mode=' "$OUT/final-boot-info.txt" >/dev/null; then
    echo "REFUSING U0g: obsolete factory module parameter is present" >&2
    exit 1
fi

recovery_sha="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
{
    cat "$REPORT"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$(stat -Lc '%s' "$CANDIDATE")"
    echo "recovery_sha256=$recovery_sha"
} | tee "$MANIFEST"

echo
echo "U0g candidate: $CANDIDATE"
echo "Manifest:      $MANIFEST"

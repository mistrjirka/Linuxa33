#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
EXPECTED_U0B_INITRAMFS_SHA256="${EXPECTED_U0B_INITRAMFS_SHA256:-e979aff2e3ee8b0485af7d1d79a899b366476924d876092154499d2e2f70d721}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-66}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INITRAMFS="$ROOT/export-debug/initramfs"
MODULE_ROOT="$ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
U0B_MODULES="$ROOT/build/u0b-embedded-modules.txt"
OUT="$ROOT/build/pmos-debug-recovery-u0c"
CANDIDATE="$ROOT/build/candidates/a33x-h1-usbpd-u0c-factory-recovery.img"
MANIFEST="$ROOT/build/candidates/a33x-h1-usbpd-u0c-factory-manifest.txt"

for command in gzip cpio sha256sum modinfo; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$INITRAMFS" \
    "$MODULE_ROOT/modules.dep" \
    "$U0B_MODULES" \
    "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

actual_initramfs_sha256="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
if [[ "$actual_initramfs_sha256" != "$EXPECTED_U0B_INITRAMFS_SHA256" ]]; then
    echo "REFUSING U0c: initramfs is not the proven U0b initramfs" >&2
    echo "Expected: $EXPECTED_U0B_INITRAMFS_SHA256" >&2
    echo "Actual:   $actual_initramfs_sha256" >&2
    exit 1
fi

pdic_module="$(
    find "$MODULE_ROOT" -type f \
        \( -name 'pdic_notifier_module.ko' \
        -o -name 'pdic_notifier_module.ko.gz' \
        -o -name 'pdic_notifier_module.ko.xz' \
        -o -name 'pdic_notifier_module.ko.zst' \) \
        -print -quit
)"

if [[ -z "$pdic_module" || ! -f "$pdic_module" ]]; then
    echo "REFUSING U0c: pdic_notifier_module was not found" >&2
    exit 1
fi

module_name="$(modinfo -F name "$pdic_module")"
if [[ -z "$module_name" ]]; then
    echo "REFUSING U0c: modinfo returned no module name" >&2
    exit 1
fi

if ! modinfo -p "$pdic_module" | grep -q '^f_usb_mode:'; then
    echo "REFUSING U0c: $module_name does not expose f_usb_mode" >&2
    modinfo -p "$pdic_module" >&2 || true
    exit 1
fi

parameter="$module_name.f_usb_mode=recovery"

current_modules="$(mktemp)"
trap 'rm -f "$current_modules"' EXIT

gzip -dc "$INITRAMFS" |
    cpio -it 2>/dev/null |
    grep -E '\.ko(\.(gz|xz|zst))?$' |
    sed -E 's#^.*/##; s/\.ko(\.(gz|xz|zst))?$//; s/-/_/g' |
    sort -u > "$current_modules"

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

if ! grep -qx "$module_name" "$current_modules"; then
    echo "REFUSING U0c: $module_name is not embedded in initramfs" >&2
    exit 1
fi

if gzip -dc "$INITRAMFS" | cpio -it 2>/dev/null |
    grep -q '^hooks/02-a33x-usbpd-load\.sh$'; then
    :
else
    echo "REFUSING U0c: U0b USB-PD loader hook is absent" >&2
    exit 1
fi

echo "=== U0c isolated delta ==="
echo "Initramfs SHA256: $actual_initramfs_sha256"
echo "Embedded modules: $module_count (identical to U0b)"
echo "Added cmdline:    $parameter"
echo "No MUIC/CPIF/BTS modules added."

env \
    ROOT="$ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="$parameter" \
    bash "$REPO_ROOT/scripts/make-pmos-debug-recovery.sh"

source_image="$OUT/recovery.img"
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$source_image" "$CANDIDATE"

if [[ "$(stat -Lc '%s' "$CANDIDATE")" != "100663296" ]]; then
    echo "REFUSING U0c: unexpected recovery image size" >&2
    exit 1
fi

if ! grep -F -- "$parameter" "$OUT/final-boot-info.txt" >/dev/null; then
    echo "REFUSING U0c: final image does not contain $parameter" >&2
    exit 1
fi

{
    echo "candidate=U0c-factory"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_delta=kernel_cmdline_usb_factory_bypass"
    echo "extra_kernel_cmdline=$parameter"
    echo "module_delta_from_u0b=none"
    echo "embedded_modules=$module_count"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
    printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
    echo "initramfs_sha256=$actual_initramfs_sha256"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$(stat -Lc '%s' "$CANDIDATE")"
    echo "recovery_sha256=$(sha256sum "$CANDIDATE" | awk '{print $1}')"
} | tee "$MANIFEST"

echo
echo "U0c candidate: $CANDIDATE"
echo "Manifest:      $MANIFEST"

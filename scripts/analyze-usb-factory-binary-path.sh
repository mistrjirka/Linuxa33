#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
MODULE_ROOT="${MODULE_ROOT:-$ROOT/build/modules-stage-safe/usr/lib/modules/$KREL}"
OUT="${OUT:-$ROOT/build/u0c-factory-binary-analysis}"

mkdir -p "$OUT"

for command in modinfo readelf nm strings; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

OBJDUMP="$(command -v llvm-objdump || command -v aarch64-linux-gnu-objdump || command -v objdump || true)"
if [[ -z "$OBJDUMP" ]]; then
    echo "Missing llvm-objdump/aarch64-linux-gnu-objdump/objdump" >&2
    exit 1
fi

find_one() {
    local name="$1"
    local result
    result="$(find "$MODULE_ROOT" -type f \
        \( -name "$name.ko" -o -name "$name.ko.gz" -o -name "$name.ko.xz" -o -name "$name.ko.zst" \) \
        -print -quit)"
    if [[ -z "$result" || ! -f "$result" ]]; then
        echo "Missing module: $name under $MODULE_ROOT" >&2
        exit 1
    fi
    printf '%s\n' "$result"
}

PDIC="$(find_one pdic_notifier_module)"
TYPEC="$(find_one usb_typec_manager)"

{
    echo "pdic=$PDIC"
    echo "typec=$TYPEC"
    echo "objdump=$OBJDUMP"
    echo
    echo "=== pdic parameters ==="
    modinfo -p "$PDIC" || true
    echo
    echo "=== Type-C manager unresolved factory symbols ==="
    nm -u "$TYPEC" 2>/dev/null |
        grep -E 'check_factory_mode_boot|is_factory_mode_pdic_param|get_usb_factory_mode' || true
    echo
    echo "=== PDIC factory symbols ==="
    readelf -sW "$PDIC" |
        grep -E 'check_factory_mode_boot|is_factory_mode_pdic_param|get_usb_factory_mode|pdic_param_factory_mode|f_usb_mode|f_mode|usb_mode' || true
    echo
    echo "=== strings around factory handling ==="
    strings -a "$PDIC" |
        grep -Ei 'factory|f_usb_mode|f_mode|pdic_param_factory_mode' || true
} | tee "$OUT/summary.txt"

FULL_DISASSEMBLY="$OUT/pdic.full-disassembly.txt"
"$OBJDUMP" -dr "$PDIC" > "$FULL_DISASSEMBLY" 2>&1

for symbol in check_factory_mode_boot is_factory_mode_pdic_param get_usb_factory_mode; do
    {
        echo "=== $symbol ==="
        awk -v symbol="$symbol" '
            $0 ~ "<" symbol ">:" { show=1 }
            show { print }
            show && /^$/ { exit }
        ' "$FULL_DISASSEMBLY"
    } > "$OUT/$symbol.disassembly.txt"
done

{
    echo "=== relocations mentioning factory symbols ==="
    readelf -rW "$PDIC" |
        grep -E 'check_factory_mode_boot|is_factory_mode_pdic_param|get_usb_factory_mode|pdic_param_factory_mode|f_usb_mode|f_mode|usb_mode' || true
    echo
    echo "=== Type-C manager relocations to factory helpers ==="
    readelf -rW "$TYPEC" |
        grep -E 'check_factory_mode_boot|is_factory_mode_pdic_param|get_usb_factory_mode' || true
} | tee "$OUT/relocations.txt"

{
    cat "$OUT/summary.txt"
    echo
    cat "$OUT/relocations.txt"
    echo
    for file in "$OUT"/*.disassembly.txt; do
        echo "##### $(basename "$file") #####"
        cat "$file"
        echo
    done
} > "$OUT/full-report.txt"

archive="$OUT.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$archive" "$(basename "$OUT")"

echo
echo "Report:  $OUT/full-report.txt"
echo "Archive: $archive"

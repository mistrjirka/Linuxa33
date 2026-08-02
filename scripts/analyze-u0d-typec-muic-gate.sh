#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
MODULE_SOURCE="${MODULE_SOURCE:-$PORT_ROOT/unpacked/twrp-root/lib/modules}"
OUT="${OUT:-$PORT_ROOT/build/u0d-typec-muic-gate-analysis}"

for command in readelf nm strings modinfo sha256sum python3 tar; do
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

TYPEC="$(
    find "$MODULE_SOURCE" -type f -name 'usb_typec_manager.ko' -print -quit
)"
if [[ -z "$TYPEC" || ! -f "$TYPEC" ]]; then
    fallback="$PORT_ROOT/build/modules-stage-safe/usr/lib/modules/$KREL/usb_typec_manager.ko"
    if [[ -f "$fallback" ]]; then
        TYPEC="$fallback"
    else
        echo "usb_typec_manager.ko not found under $MODULE_SOURCE or the staged tree" >&2
        exit 1
    fi
fi

rm -rf "$OUT"
mkdir -p "$OUT"

modinfo "$TYPEC" > "$OUT/modinfo.txt"
readelf -hW "$TYPEC" > "$OUT/elf-header.txt"
readelf -SW "$TYPEC" > "$OUT/sections.txt"
readelf -sW "$TYPEC" > "$OUT/symbols.txt"
readelf -rW "$TYPEC" > "$OUT/relocations.txt"
nm -an "$TYPEC" > "$OUT/nm-all.txt"
strings -a -t x "$TYPEC" > "$OUT/strings-offsets.txt"
"$OBJDUMP" -dr "$TYPEC" > "$OUT/full-disassembly.txt"

python3 - "$OUT/full-disassembly.txt" "$OUT" <<'PY'
from __future__ import annotations

from pathlib import Path
import re
import sys

full_path = Path(sys.argv[1])
out = Path(sys.argv[2])
lines = full_path.read_text(errors="replace").splitlines()

symbols = [
    "manager_usb_event_send",
    "manager_handle_pdic_notification",
    "manager_notifier_register",
    "manager_handle_usb_event",
    "manager_event_notify",
]

header_re = re.compile(r"^[0-9a-fA-F]+ <([^>]+)>:$")

for symbol in symbols:
    selected: list[str] = []
    active = False
    for line in lines:
        match = header_re.match(line.strip())
        if match:
            current = match.group(1)
            if active and current != symbol:
                break
            if current == symbol:
                active = True
        if active:
            selected.append(line)

    destination = out / f"{symbol}.disassembly.txt"
    if not selected:
        destination.write_text(f"SYMBOL_NOT_FOUND: {symbol}\n")
    else:
        destination.write_text("\n".join(selected) + "\n")
PY

python3 - "$OUT/strings-offsets.txt" "$OUT/relevant-strings.txt" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(errors="replace").splitlines()
needles = (
    "Duplicate event",
    "Skip event",
    "usb_factory mode",
    "Forced to run usb",
    "USB_ATTACH_UFP",
    "manager_usb_event_send",
    "muic_none",
    "muic_usb",
    "muic_otg",
    "muic_timeout_open_device",
)
selected = [line for line in source if any(needle in line for needle in needles)]
Path(sys.argv[2]).write_text("\n".join(selected) + ("\n" if selected else ""))
PY

python3 - "$OUT/symbols.txt" "$OUT/relevant-symbols.txt" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(errors="replace").splitlines()
needles = (
    "manager_usb_event_send",
    "manager_handle_pdic_notification",
    "manager_notifier_register",
    "manager_handle_usb_event",
    "typec_manager",
)
selected = [line for line in source if any(needle in line for needle in needles)]
Path(sys.argv[2]).write_text("\n".join(selected) + ("\n" if selected else ""))
PY

python3 - "$OUT/relocations.txt" "$OUT/relevant-relocations.txt" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1]).read_text(errors="replace").splitlines()
needles = (
    "manager_usb_event_send",
    "manager_notify_string",
    "pdic_usbstatus_string",
    "manager_handle_usb_event",
    "typec_manager",
    ".rodata",
)
selected = [line for line in source if any(needle in line for needle in needles)]
Path(sys.argv[2]).write_text("\n".join(selected) + ("\n" if selected else ""))
PY

{
    echo "module=$TYPEC"
    echo "module_sha256=$(sha256sum "$TYPEC" | awk '{print $1}')"
    echo "module_name=$(modinfo -F name "$TYPEC")"
    echo "vermagic=$(modinfo -F vermagic "$TYPEC")"
    echo "objdump=$OBJDUMP"
    echo
    echo "=== relevant symbols ==="
    cat "$OUT/relevant-symbols.txt"
    echo
    echo "=== relevant strings ==="
    cat "$OUT/relevant-strings.txt"
    echo
    echo "=== manager_usb_event_send ==="
    cat "$OUT/manager_usb_event_send.disassembly.txt"
    echo
    echo "=== manager_notifier_register ==="
    cat "$OUT/manager_notifier_register.disassembly.txt"
    echo
    echo "=== manager_handle_pdic_notification ==="
    cat "$OUT/manager_handle_pdic_notification.disassembly.txt"
} | tee "$OUT/report.txt"

archive="$OUT.tar.gz"
tar -C "$(dirname "$OUT")" -czf "$archive" "$(basename "$OUT")"

echo
echo "Report:  $OUT/report.txt"
echo "Archive: $archive"

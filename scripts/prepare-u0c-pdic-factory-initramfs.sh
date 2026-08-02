#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-66}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"

for command in pmbootstrap sudo sha256sum gzip cpio cmp python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

if [[ ! -d "$ROOTFS" ]]; then
    echo "Missing rootfs: $ROOTFS" >&2
    exit 1
fi
if [[ ! -f "$PATCHER" ]]; then
    echo "Missing patcher: $PATCHER" >&2
    exit 1
fi
if [[ ! -f "$PORT_ROOT/build/u0b-embedded-modules.txt" ]]; then
    echo "Missing U0b module baseline" >&2
    exit 1
fi

echo "=== Prepare reproducible patched module package ==="
A33X_PDIC_FACTORY_PATCH=1 \
    bash "$REPO_ROOT/scripts/prepare-safe-module-packages.sh"

STAGED_ROOT="$PORT_ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
STAGED_PDIC="$(find "$STAGED_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"
ROOTFS_PDIC="$(find "$ROOTFS/usr/lib/modules/$KREL" -type f -name 'pdic_notifier_module.ko' -print -quit)"

if [[ -z "$STAGED_PDIC" || ! -f "$STAGED_PDIC" ]]; then
    echo "Missing staged patched PDIC module" >&2
    exit 1
fi
if [[ -z "$ROOTFS_PDIC" || ! -f "$ROOTFS_PDIC" ]]; then
    echo "Missing rootfs PDIC module" >&2
    exit 1
fi

python3 "$PATCHER" --module "$STAGED_PDIC" --verify-patched >/dev/null

BACKUP_DIR="$PORT_ROOT/build/u0c-rootfs-backup"
mkdir -p "$BACKUP_DIR"
BACKUP_PDIC="$BACKUP_DIR/pdic_notifier_module.ko.before-u0c"
if [[ ! -f "$BACKUP_PDIC" ]]; then
    sudo cp -a "$ROOTFS_PDIC" "$BACKUP_PDIC"
    sudo chown "$(id -u):$(id -g)" "$BACKUP_PDIC"
fi

if python3 "$PATCHER" --module "$ROOTFS_PDIC" --verify-patched >/dev/null 2>&1; then
    echo "Rootfs PDIC module was already patched; replacing with staged exact copy."
else
    echo "Rootfs PDIC module is currently unpatched, as expected."
fi

sudo install -m 0644 "$STAGED_PDIC" "$ROOTFS_PDIC"
if ! sudo cmp -s "$STAGED_PDIC" "$ROOTFS_PDIC"; then
    echo "REFUSING: rootfs PDIC copy verification failed" >&2
    exit 1
fi

for hook in \
    "$ROOTFS/usr/share/mkinitfs/hooks/01-a33x-watchdog.sh" \
    "$ROOTFS/usr/share/mkinitfs/hooks/02-a33x-usbpd-load.sh"
do
    if [[ ! -f "$hook" ]]; then
        echo "REFUSING: required initramfs hook is missing: $hook" >&2
        exit 1
    fi
done

echo "=== Rebuild initramfs in existing rootfs ==="
pmbootstrap chroot -r -- sh -ec "depmod -a '$KREL'; mkinitfs"

ROOTFS_INITRAMFS="$ROOTFS/boot/initramfs"
if [[ ! -f "$ROOTFS_INITRAMFS" ]]; then
    echo "mkinitfs did not produce $ROOTFS_INITRAMFS" >&2
    exit 1
fi

echo "=== Export patched initramfs ==="
rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"

INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    echo "Exported initramfs is missing: $INITRAMFS" >&2
    exit 1
fi

current_modules="$(mktemp)"
extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
list_errors="$extract_dir/cpio-list.stderr"
extract_errors="$extract_dir/cpio-extract.stderr"
trap 'rm -f "$current_modules"; rm -rf "$extract_dir"' EXIT

echo "=== Inspect exported initramfs ==="
if ! gzip -dc "$INITRAMFS" > "$cpio_archive"; then
    echo "REFUSING: failed to decompress exported initramfs" >&2
    exit 1
fi
if [[ ! -s "$cpio_archive" ]]; then
    echo "REFUSING: decompressed initramfs is empty" >&2
    exit 1
fi
if ! cpio -it < "$cpio_archive" > "$entries" 2> "$list_errors"; then
    echo "REFUSING: cpio could not list exported initramfs" >&2
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

actual_module_count="$(wc -l < "$current_modules")"
if [[ "$actual_module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING: exported initramfs module count changed: expected $EXPECTED_MODULE_COUNT, found $actual_module_count" >&2
    exit 1
fi
if ! cmp -s "$PORT_ROOT/build/u0b-embedded-modules.txt" "$current_modules"; then
    echo "REFUSING: exported module names differ from U0b" >&2
    diff -u "$PORT_ROOT/build/u0b-embedded-modules.txt" "$current_modules" >&2 || true
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
        echo "REFUSING: failed to extract $pdic_entry" >&2
        cat "$extract_errors" >&2 || true
        exit 1
    fi
)
EMBEDDED_PDIC="$extract_dir/$pdic_entry"
if [[ ! -f "$EMBEDDED_PDIC" ]]; then
    echo "REFUSING: extracted PDIC module is missing: $EMBEDDED_PDIC" >&2
    exit 1
fi
python3 "$PATCHER" --module "$EMBEDDED_PDIC" --verify-patched >/dev/null
if ! cmp -s "$STAGED_PDIC" "$EMBEDDED_PDIC"; then
    echo "REFUSING: embedded patched module differs from staged module" >&2
    exit 1
fi

echo "=== Verify U0c initramfs safety and activation ==="
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-driver.sh" "$INITRAMFS"
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-hook.sh" "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-initramfs-safety.py" \
    --initramfs "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-module-activation.py" \
    --contracts "$REPO_ROOT/config/module-activation-contracts.tsv" \
    --repo-root "$REPO_ROOT" \
    --initramfs "$INITRAMFS"

echo
echo "=== U0c initramfs ready ==="
echo "initramfs=$INITRAMFS"
echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
echo "patched_module_sha256=$(sha256sum "$EMBEDDED_PDIC" | awk '{print $1}')"
echo "embedded_modules=$EXPECTED_MODULE_COUNT"

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-66}"
ORIGINAL_PDIC_SHA256="${ORIGINAL_PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TYPEC_PATCHER="$REPO_ROOT/scripts/patch-typec-muic-none-mask.py"
PDIC_PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"

for command in pmbootstrap sudo sha256sum gzip cpio cmp python3 depmod modinfo tar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$ROOTFS" \
    "$TYPEC_PATCHER" \
    "$PDIC_PATCHER" \
    "$PORT_ROOT/build/u0b-embedded-modules.txt"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required path: $required" >&2
        exit 1
    fi
done

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
KPKG="${KPKG:-$PMAPORTS/device/downstream/linux-samsung-a33x}"

if [[ ! -f "$KPKG/APKBUILD" ]]; then
    echo "Missing kernel package APKBUILD: $KPKG/APKBUILD" >&2
    exit 1
fi

echo "=== Prepare original 66-module package tree ==="
A33X_PDIC_FACTORY_PATCH=0 \
    bash "$REPO_ROOT/scripts/prepare-safe-module-packages.sh"

STAGED_ROOT="$PORT_ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
STAGED_TYPEC="$(find "$STAGED_ROOT" -type f -name 'usb_typec_manager.ko' -print -quit)"
STAGED_PDIC="$(find "$STAGED_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"

for required in "$STAGED_TYPEC" "$STAGED_PDIC" "$STAGED_ROOT/modules.dep"; do
    if [[ -z "$required" || ! -f "$required" ]]; then
        echo "Missing staged module artifact: $required" >&2
        exit 1
    fi
done

python3 "$TYPEC_PATCHER" --module "$STAGED_TYPEC" --verify-original >/dev/null

staged_pdic_sha="$(sha256sum "$STAGED_PDIC" | awk '{print $1}')"
if [[ "$staged_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0d: staged PDIC module is not the original U0b binary" >&2
    echo "Expected: $ORIGINAL_PDIC_SHA256" >&2
    echo "Actual:   $staged_pdic_sha" >&2
    exit 1
fi

# The U0c patch verifier must reject the restored original module.
if python3 "$PDIC_PATCHER" --module "$STAGED_PDIC" --verify-patched >/dev/null 2>&1; then
    echo "REFUSING U0d: staged PDIC module still contains the U0c factory patch" >&2
    exit 1
fi

echo "=== Apply isolated U0d Type-C MUIC_NONE mask patch ==="

before_name="$(modinfo -F name "$STAGED_TYPEC")"
before_vermagic="$(modinfo -F vermagic "$STAGED_TYPEC")"
before_depends="$(modinfo -F depends "$STAGED_TYPEC")"

PATCHED_TYPEC="${STAGED_TYPEC%.ko}.patched.ko"
rm -f "$PATCHED_TYPEC"
python3 "$TYPEC_PATCHER" \
    --module "$STAGED_TYPEC" \
    --output "$PATCHED_TYPEC" \
    --report "$PORT_ROOT/build/u0d-typec-muic-none-patch.txt"

after_name="$(modinfo -F name "$PATCHED_TYPEC")"
after_vermagic="$(modinfo -F vermagic "$PATCHED_TYPEC")"
after_depends="$(modinfo -F depends "$PATCHED_TYPEC")"

if [[ "$before_name" != "$after_name" \
    || "$before_vermagic" != "$after_vermagic" \
    || "$before_depends" != "$after_depends" ]]
then
    echo "REFUSING U0d: Type-C module metadata changed unexpectedly" >&2
    exit 1
fi

mv "$PATCHED_TYPEC" "$STAGED_TYPEC"
python3 "$TYPEC_PATCHER" --module "$STAGED_TYPEC" --verify-patched >/dev/null

echo "Type-C MUIC_NONE mask patch verified: $STAGED_TYPEC"
echo "PDIC factory module restored to exact original: $staged_pdic_sha"

# Recreate dependency indexes and package payload after changing the staged ELF.
depmod -b "$PORT_ROOT/build/modules-stage-safe" -m /usr/lib/modules "$KREL"
test -s "$STAGED_ROOT/modules.dep"

tar -C "$PORT_ROOT/build/modules-stage-safe/usr/lib" \
    -czf "$KPKG/modules.tar.gz" \
    modules

echo "Repacked patched modules.tar.gz: $KPKG/modules.tar.gz"

ROOTFS_ROOT="$ROOTFS/usr/lib/modules/$KREL"
ROOTFS_TYPEC="$(find "$ROOTFS_ROOT" -type f -name 'usb_typec_manager.ko' -print -quit)"
ROOTFS_PDIC="$(find "$ROOTFS_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit)"

for required in "$ROOTFS_TYPEC" "$ROOTFS_PDIC"; do
    if [[ -z "$required" || ! -f "$required" ]]; then
        echo "Missing rootfs module artifact: $required" >&2
        exit 1
    fi
done

BACKUP_DIR="$PORT_ROOT/build/u0d-rootfs-backup"
mkdir -p "$BACKUP_DIR"
if [[ ! -f "$BACKUP_DIR/usb_typec_manager.ko.before-u0d" ]]; then
    sudo cp -a "$ROOTFS_TYPEC" "$BACKUP_DIR/usb_typec_manager.ko.before-u0d"
    sudo chown "$(id -u):$(id -g)" "$BACKUP_DIR/usb_typec_manager.ko.before-u0d"
fi
if [[ ! -f "$BACKUP_DIR/pdic_notifier_module.ko.before-u0d" ]]; then
    sudo cp -a "$ROOTFS_PDIC" "$BACKUP_DIR/pdic_notifier_module.ko.before-u0d"
    sudo chown "$(id -u):$(id -g)" "$BACKUP_DIR/pdic_notifier_module.ko.before-u0d"
fi

echo "=== Install patched Type-C and restored original PDIC modules ==="
sudo install -m 0644 "$STAGED_TYPEC" "$ROOTFS_TYPEC"
sudo install -m 0644 "$STAGED_PDIC" "$ROOTFS_PDIC"

if ! sudo cmp -s "$STAGED_TYPEC" "$ROOTFS_TYPEC"; then
    echo "REFUSING U0d: rootfs Type-C copy verification failed" >&2
    exit 1
fi
if ! sudo cmp -s "$STAGED_PDIC" "$ROOTFS_PDIC"; then
    echo "REFUSING U0d: rootfs PDIC copy verification failed" >&2
    exit 1
fi

python3 "$TYPEC_PATCHER" --module "$ROOTFS_TYPEC" --verify-patched >/dev/null
rootfs_pdic_sha="$(sudo sha256sum "$ROOTFS_PDIC" | awk '{print $1}')"
if [[ "$rootfs_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0d: rootfs PDIC module was not restored" >&2
    exit 1
fi

for hook in \
    "$ROOTFS/usr/share/mkinitfs/hooks/01-a33x-watchdog.sh" \
    "$ROOTFS/usr/share/mkinitfs/hooks/02-a33x-usbpd-load.sh"
do
    if [[ ! -f "$hook" ]]; then
        echo "REFUSING U0d: required initramfs hook is missing: $hook" >&2
        exit 1
    fi
done

echo "=== Rebuild U0d initramfs in existing rootfs ==="
pmbootstrap chroot -r -- sh -ec "depmod -a '$KREL'; mkinitfs"

ROOTFS_INITRAMFS="$ROOTFS/boot/initramfs"
if [[ ! -f "$ROOTFS_INITRAMFS" ]]; then
    echo "mkinitfs did not produce $ROOTFS_INITRAMFS" >&2
    exit 1
fi

echo "=== Export U0d initramfs ==="
rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"

INITRAMFS="$EXPORT_DIR/initramfs"
if [[ ! -f "$INITRAMFS" ]]; then
    echo "Exported initramfs is missing: $INITRAMFS" >&2
    exit 1
fi

extract_dir="$(mktemp -d)"
cpio_archive="$extract_dir/initramfs.cpio"
entries="$extract_dir/entries.txt"
list_errors="$extract_dir/cpio-list.stderr"
extract_errors="$extract_dir/cpio-extract.stderr"
current_modules="$extract_dir/current-modules.txt"
trap 'rm -rf "$extract_dir"' EXIT

echo "=== Inspect exported U0d initramfs ==="
gzip -dc "$INITRAMFS" > "$cpio_archive"
if [[ ! -s "$cpio_archive" ]]; then
    echo "REFUSING U0d: decompressed initramfs is empty" >&2
    exit 1
fi
if ! cpio -it < "$cpio_archive" > "$entries" 2> "$list_errors"; then
    echo "REFUSING U0d: cpio could not list exported initramfs" >&2
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

python3 - "$PORT_ROOT/build/u0b-embedded-modules.txt" "$current_modules" "$EXPECTED_MODULE_COUNT" <<'PY'
from pathlib import Path
import sys

baseline = {line.strip() for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()}
current = {line.strip() for line in Path(sys.argv[2]).read_text().splitlines() if line.strip()}
expected = int(sys.argv[3])
if len(current) != expected:
    raise SystemExit(f"U0d module count changed: expected {expected}, found {len(current)}")
if current != baseline:
    missing = sorted(baseline - current)
    added = sorted(current - baseline)
    raise SystemExit(f"U0d module set differs from U0b: missing={missing}, added={added}")
print(f"U0d module set matches U0b: {len(current)} modules")
PY

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
    echo "REFUSING U0d: embedded Type-C module differs from staged patched module" >&2
    exit 1
fi

embedded_pdic_sha="$(sha256sum "$EMBEDDED_PDIC" | awk '{print $1}')"
if [[ "$embedded_pdic_sha" != "$ORIGINAL_PDIC_SHA256" ]]; then
    echo "REFUSING U0d: embedded PDIC module still differs from original" >&2
    exit 1
fi
if ! cmp -s "$STAGED_PDIC" "$EMBEDDED_PDIC"; then
    echo "REFUSING U0d: embedded PDIC differs from staged original module" >&2
    exit 1
fi

echo "=== Verify U0d initramfs safety and activation ==="
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-driver.sh" "$INITRAMFS"
bash "$REPO_ROOT/scripts/verify-initramfs-watchdog-hook.sh" "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-initramfs-safety.py" --initramfs "$INITRAMFS"
python3 "$REPO_ROOT/scripts/verify-module-activation.py" \
    --contracts "$REPO_ROOT/config/module-activation-contracts.tsv" \
    --repo-root "$REPO_ROOT" \
    --initramfs "$INITRAMFS"

echo
echo "=== U0d initramfs ready ==="
echo "initramfs=$INITRAMFS"
echo "initramfs_size=$(stat -Lc '%s' "$INITRAMFS")"
printf 'initramfs_marker=0x%08x\n' "$(stat -Lc '%s' "$INITRAMFS")"
echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
echo "patched_typec_sha256=$(sha256sum "$EMBEDDED_TYPEC" | awk '{print $1}')"
echo "original_pdic_sha256=$embedded_pdic_sha"
echo "embedded_modules=$EXPECTED_MODULE_COUNT"

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
PMBOOTSTRAP_WORK="${PMBOOTSTRAP_WORK:-$HOME/.local/var/pmbootstrap}"
ROOTFS="${ROOTFS:-$PMBOOTSTRAP_WORK/chroot_rootfs_samsung-a33x}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE="$SCRIPT_DIR/prepare-u0e-muic-switch-initramfs.sh"
RUNTIME="$SCRIPT_DIR/.prepare-u0e-muic-switch-initramfs.runtime.$$"
STATE_FILE="$PORT_ROOT/build/third-host-pmbootstrap-state.txt"
SAFE_PREP="$SCRIPT_DIR/prepare-safe-module-packages.sh"
BASELINE_MODULES="$PORT_ROOT/build/u0b-embedded-modules.txt"

BASE_HOOK_PACKAGES="postmarketos-mkinitfs-hook-a33x-watchdog,postmarketos-mkinitfs-hook-a33x-usbpd,postmarketos-mkinitfs-hook-debug-shell"
CUSTOM_BASE_HOOK_PACKAGES=(
    postmarketos-mkinitfs-hook-a33x-watchdog
    postmarketos-mkinitfs-hook-a33x-usbpd
)
EXPERIMENT_PACKAGES=(
    postmarketos-mkinitfs-hook-a33x-muic-switch
    postmarketos-mkinitfs-hook-a33x-muic-persist
)

for command in pmbootstrap python3 sudo; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in "$SOURCE" "$SAFE_PREP" "$BASELINE_MODULES"; do
    if [[ ! -f "$required" ]]; then
        echo "Missing required file: $required" >&2
        exit 1
    fi
done
if [[ ! -d "$ROOTFS" ]]; then
    echo "Missing A33 rootfs chroot: $ROOTFS" >&2
    exit 1
fi

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
DPKG="$PMAPORTS/device/downstream/device-samsung-a33x"
ROOTFS_MODULE_LIST="$ROOTFS/usr/share/mkinitfs/modules/00-device-samsung-a33x.modules"

cleanup() {
    rm -f "$RUNTIME"
}
trap cleanup EXIT

if [[ -f "$STATE_FILE" ]]; then
    mv -f "$STATE_FILE" "$STATE_FILE.invalid"
    echo "Quarantined unverified state marker: $STATE_FILE.invalid"
fi

echo "=== Reset exact U0d rootfs state before U0e ==="
for package in "${EXPERIMENT_PACKAGES[@]}"; do
    if pmbootstrap chroot -r -- apk info -e "$package" >/dev/null 2>&1; then
        echo "Removing stale experiment package: $package"
        pmbootstrap chroot -r -- apk del "$package"
    fi
done

# Remove only files owned by the U0e/U0f experiment packages. This also
# handles a partially installed package whose APK database entry is missing.
sudo rm -f \
    "$ROOTFS/usr/libexec/a33x-muic-switch" \
    "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch.sh" \
    "$ROOTFS/usr/share/mkinitfs/files/03-a33x-muic-switch.files" \
    "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh"

# prepare-safe-module-packages regenerates the authoritative U0b/U0d
# 66-module profile. Synchronize that exact profile into the already-created
# rootfs before U0d invokes mkinitfs; otherwise i2c_dev from a previous U0e
# run remains in 00-device-samsung-a33x.modules and contaminates the baseline.
A33X_PDIC_FACTORY_PATCH=0 bash "$SAFE_PREP"

if [[ ! -f "$DPKG/modules-initfs" ]]; then
    echo "Missing regenerated device module profile: $DPKG/modules-initfs" >&2
    exit 1
fi
if [[ ! -f "$ROOTFS_MODULE_LIST" ]]; then
    echo "Missing rootfs device module profile: $ROOTFS_MODULE_LIST" >&2
    exit 1
fi

sudo install -m 0644 "$DPKG/modules-initfs" "$ROOTFS_MODULE_LIST"

python3 - "$BASELINE_MODULES" "$DPKG/modules-initfs" "$ROOTFS_MODULE_LIST" <<'PY'
from pathlib import Path
import re
import sys


def normalized(path: str) -> set[str]:
    result: set[str] = set()
    for raw in Path(path).read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = Path(line).name
        name = re.sub(r"\.(?:gz|xz|zst)$", "", name)
        name = re.sub(r"\.ko$", "", name)
        result.add(name.replace("-", "_"))
    return result

baseline = normalized(sys.argv[1])
device = normalized(sys.argv[2])
rootfs = normalized(sys.argv[3])

if len(baseline) != 66:
    raise SystemExit(f"REFUSING: U0b baseline must contain 66 modules, found {len(baseline)}")
if device != baseline:
    raise SystemExit(
        "REFUSING: regenerated device profile differs from U0b: "
        f"missing={sorted(baseline - device)} added={sorted(device - baseline)}"
    )
if rootfs != baseline:
    raise SystemExit(
        "REFUSING: synchronized rootfs profile differs from U0b: "
        f"missing={sorted(baseline - rootfs)} added={sorted(rootfs - baseline)}"
    )
if "i2c_dev" in rootfs:
    raise SystemExit("REFUSING: i2c_dev remained in the U0d rootfs baseline")

print("Exact U0d rootfs module profile verified: 66 modules")
PY

for stale in \
    "$ROOTFS/usr/libexec/a33x-muic-switch" \
    "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch.sh" \
    "$ROOTFS/usr/share/mkinitfs/files/03-a33x-muic-switch.files" \
    "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh"
do
    if [[ -e "$stale" ]]; then
        echo "REFUSING: stale experiment artifact remains in U0d rootfs: $stale" >&2
        exit 1
    fi
done

echo "=== Refresh checksums for local U0d base hooks ==="
for package in "${CUSTOM_BASE_HOOK_PACKAGES[@]}"; do
    pmbootstrap checksum "$package"
done

echo "=== Install and verify U0d base initramfs hooks ==="
pmbootstrap chroot -r --add "$BASE_HOOK_PACKAGES" -- true

pmbootstrap chroot -r -- sh -ec '
apk info -e postmarketos-mkinitfs-hook-a33x-watchdog
apk info -e postmarketos-mkinitfs-hook-a33x-usbpd
apk info -e postmarketos-mkinitfs-hook-debug-shell

test -x /usr/share/mkinitfs/hooks/01-a33x-watchdog.sh
test -x /usr/share/mkinitfs/hooks/02-a33x-usbpd-load.sh
test -f /usr/share/mkinitfs/files/20-debug-shell.files

echo "U0d base hooks verified"
'

# The underlying U0e script predates pmbootstrap's local-package resolver and
# invokes raw apk for the locally built MUIC helper. Create a temporary exact
# runtime copy in which that one command uses `pmbootstrap chroot --add`.
python3 - "$SOURCE" "$RUNTIME" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
runtime = Path(sys.argv[2])
text = source.read_text()
old = 'pmbootstrap chroot -r -- apk add --upgrade "$PACKAGE"'
new = 'pmbootstrap chroot -r --add "$PACKAGE" -- true'
count = text.count(old)
if count != 1:
    raise SystemExit(
        f"REFUSING: expected exactly one raw local APK install command, found {count}"
    )
runtime.write_text(text.replace(old, new))
runtime.chmod(0o755)
PY

bash "$RUNTIME" "$@"

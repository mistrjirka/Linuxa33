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

BASE_HOOK_PACKAGES="postmarketos-mkinitfs-hook-a33x-watchdog,postmarketos-mkinitfs-hook-a33x-usbpd,postmarketos-mkinitfs-hook-debug-shell"
CUSTOM_BASE_HOOK_PACKAGES=(
    postmarketos-mkinitfs-hook-a33x-watchdog
    postmarketos-mkinitfs-hook-a33x-usbpd
)

for command in pmbootstrap python3; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

if [[ ! -f "$SOURCE" ]]; then
    echo "Missing U0e preparation script: $SOURCE" >&2
    exit 1
fi
if [[ ! -d "$ROOTFS" ]]; then
    echo "Missing A33 rootfs chroot: $ROOTFS" >&2
    exit 1
fi

cleanup() {
    rm -f "$RUNTIME"
}
trap cleanup EXIT

if [[ -f "$STATE_FILE" ]]; then
    mv -f "$STATE_FILE" "$STATE_FILE.invalid"
    echo "Quarantined unverified state marker: $STATE_FILE.invalid"
fi

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

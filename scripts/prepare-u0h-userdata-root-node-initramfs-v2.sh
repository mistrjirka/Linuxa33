#!/usr/bin/env bash
set -Eeuo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SOURCE_U0G="$SCRIPT_DIR/prepare-u0g-muic-dynamic-initramfs.sh"
SOURCE_U0H="$SCRIPT_DIR/prepare-u0h-userdata-root-node-initramfs.sh"
TOKEN="$$.$RANDOM"
TMP_U0G="$SCRIPT_DIR/.prepare-u0g-idempotent-$TOKEN.sh"
TMP_U0H="$SCRIPT_DIR/.prepare-u0h-idempotent-$TOKEN.sh"

for command in bash cp rm python3; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$SOURCE_U0G" "$SOURCE_U0H"; do
    [[ -f "$required" ]] || {
        echo "Missing required preparation script: $required" >&2
        exit 1
    }
done

cleanup() {
    rm -f "$TMP_U0G" "$TMP_U0H"
}
trap cleanup EXIT

cp "$SOURCE_U0G" "$TMP_U0G"
cp "$SOURCE_U0H" "$TMP_U0H"

python3 - "$TMP_U0G" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

replacements = [
    (
        '''for package in \\
    postmarketos-mkinitfs-hook-a33x-muic-switch \\
    postmarketos-mkinitfs-hook-a33x-muic-persist \\
    "$SWITCH_PACKAGE" \\
    "$PERSIST_PACKAGE"
do
    remove_package_if_present "$package"
done
''',
        '''for package in \\
    postmarketos-mkinitfs-hook-a33x-muic-switch \\
    postmarketos-mkinitfs-hook-a33x-muic-persist
do
    remove_package_if_present "$package"
done
''',
    ),
    (
        '''for stale in \\
    "$ROOTFS/usr/libexec/a33x-muic-switch" \\
    "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch.sh" \\
    "$ROOTFS/usr/share/mkinitfs/files/03-a33x-muic-switch.files" \\
    "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh" \\
    "$ROOTFS_HELPER" "$ROOTFS_HOOK03" "$ROOTFS_HOOK04"
do
''',
        '''for stale in \\
    "$ROOTFS/usr/libexec/a33x-muic-switch" \\
    "$ROOTFS/usr/share/mkinitfs/hooks/03-a33x-muic-switch.sh" \\
    "$ROOTFS/usr/share/mkinitfs/files/03-a33x-muic-switch.files" \\
    "$ROOTFS/usr/share/mkinitfs/hooks/04-a33x-muic-persist.sh"
do
''',
    ),
    (
        'pmbootstrap chroot -r --add "$SWITCH_PACKAGE,$PERSIST_PACKAGE" -- true\n',
        '''pmbootstrap chroot -r --add "$SWITCH_PACKAGE,$PERSIST_PACKAGE" -- true

# These packages are reproducible dependencies of device-samsung-a33x and may
# already be installed at the same version. Reinstall them from the freshly
# rebuilt local repository instead of trying to remove them and break the
# device-package dependency closure.
pmbootstrap chroot -r -- \\
    apk fix --force-overwrite "$SWITCH_PACKAGE" "$PERSIST_PACKAGE"
''',
    ),
]

for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"expected one exact U0g idempotency patch target, found {count}: {old[:80]!r}"
        )
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
PY

python3 - "$TMP_U0H" "$TMP_U0G" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
patched_u0g = sys.argv[2]
text = path.read_text(encoding="utf-8")
old = 'U0G_PREP="$SCRIPT_DIR/prepare-u0g-muic-dynamic-initramfs.sh"'
new = f'U0G_PREP="{patched_u0g}"'
count = text.count(old)
if count != 1:
    raise SystemExit(f"expected one U0G_PREP assignment, found {count}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
PY

bash -n "$TMP_U0G"
bash -n "$TMP_U0H"

bash "$TMP_U0H"

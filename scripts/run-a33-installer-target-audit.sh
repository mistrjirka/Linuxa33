#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-normal-rootfs}"
ZIP_LINK="$EXPORT_DIR/pmos-samsung-a33x.zip"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUDIT="$SCRIPT_DIR/audit-a33-recovery-installer-target.sh"
PREPARE="$SCRIPT_DIR/prepare-a33-normal-rootfs-installer.sh"

for required in "$AUDIT" "$PREPARE"; do
    [[ -f "$required" ]] || {
        echo "Missing required script: $required" >&2
        exit 1
    }
done

if [[ ! -f "$ZIP_LINK" ]]; then
    echo "=== Refresh host export links ==="
    mkdir -p "$EXPORT_DIR"
    pmbootstrap export --no-install "$EXPORT_DIR" >/dev/null 2>&1 || true
fi

if [[ ! -f "$ZIP_LINK" ]]; then
    resolved="$(readlink -f "$ZIP_LINK" 2>/dev/null || true)"
    cat >&2 <<EOF
REFUSING: the host-side postmarketOS recovery installer has not been generated.
link=$ZIP_LINK
resolved=${resolved:-missing}

Generate and validate it first with:
  bash $PREPARE

That preparation performs no phone partition writes. Then rerun this wrapper.
EOF
    exit 2
fi

EXPORT_DIR="$EXPORT_DIR" bash "$AUDIT"

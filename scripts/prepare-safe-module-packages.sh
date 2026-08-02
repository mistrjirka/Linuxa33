#!/usr/bin/env bash
set -euo pipefail

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTIVATION_CHECKER="$REPO_ROOT/scripts/verify-module-activation.py"
ACTIVATION_CONTRACTS="$REPO_ROOT/config/module-activation-contracts.tsv"

if ! command -v pmbootstrap >/dev/null 2>&1; then
    echo "pmbootstrap is not available in PATH" >&2
    exit 1
fi
if ! command -v depmod >/dev/null 2>&1; then
    echo "depmod is not available; install kmod" >&2
    exit 1
fi
if ! command -v modinfo >/dev/null 2>&1; then
    echo "modinfo is not available; install kmod" >&2
    exit 1
fi

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
MODULE_SOURCE="${MODULE_SOURCE:-$PORT_ROOT/unpacked/twrp-root/lib/modules}"
KPKG="${KPKG:-$PMAPORTS/device/downstream/linux-samsung-a33x}"
DPKG="${DPKG:-$PMAPORTS/device/downstream/device-samsung-a33x}"
STAGE="${STAGE:-$PORT_ROOT/build/modules-stage-safe}"

for required in \
    "$MODULE_SOURCE/modules.load.recovery" \
    "$KPKG/APKBUILD" \
    "$DPKG/APKBUILD" \
    "$REPO_ROOT/scripts/generate-modules-initfs.py" \
    "$ACTIVATION_CHECKER" \
    "$ACTIVATION_CONTRACTS"
do
    if [[ ! -f "$required" ]]; then
        echo "Missing required file: $required" >&2
        exit 1
    fi
done

rm -rf "$STAGE"
mkdir -p "$STAGE/usr/lib/modules/$KREL"

cp -a "$MODULE_SOURCE/." "$STAGE/usr/lib/modules/$KREL/"

echo "=== Generate module dependency indexes ==="
depmod \
    -b "$STAGE" \
    -m /usr/lib/modules \
    "$KREL"

MODULE_ROOT="$STAGE/usr/lib/modules/$KREL"
test -s "$MODULE_ROOT/modules.dep"

echo
echo "=== Generate guarded modules-initfs ==="
python3 "$REPO_ROOT/scripts/generate-modules-initfs.py" \
    --module-root "$MODULE_ROOT" \
    --output "$DPKG/modules-initfs" \
    --report "$PORT_ROOT/build/modules-initfs-safe.report.txt"

echo
echo "=== Verify module activation contracts ==="
python3 "$ACTIVATION_CHECKER" \
    --contracts "$ACTIVATION_CONTRACTS" \
    --repo-root "$REPO_ROOT" \
    --selected-modules "$DPKG/modules-initfs" \
    --module-root "$MODULE_ROOT"

echo
echo "=== Package complete module tree for the root filesystem ==="
tar -C "$STAGE/usr/lib" \
    -czf "$KPKG/modules.tar.gz" \
    modules

echo
echo "=== Safety summary ==="
printf 'modules-initfs entries: '
wc -l < "$DPKG/modules-initfs"

if grep -Ei 'phy[-_]exynos[-_]mipi|exynos[-_]drm|mcd[-_]panel|fimc[-_]is' \
    "$DPKG/modules-initfs"
then
    echo "REFUSING: unsafe module appeared in modules-initfs" >&2
    exit 1
fi

echo "No known unsafe MIPI/display/camera modules selected."
echo "modules.tar.gz: $KPKG/modules.tar.gz"
echo "modules-initfs: $DPKG/modules-initfs"
echo "report: $PORT_ROOT/build/modules-initfs-safe.report.txt"
echo
echo "Next: run pmbootstrap checksum for both A33 packages."

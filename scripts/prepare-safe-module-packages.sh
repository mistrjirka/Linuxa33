#!/usr/bin/env bash
set -euo pipefail

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
A33X_PDIC_FACTORY_PATCH="${A33X_PDIC_FACTORY_PATCH:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ACTIVATION_CHECKER="$REPO_ROOT/scripts/verify-module-activation.py"
ACTIVATION_CONTRACTS="$REPO_ROOT/config/module-activation-contracts.tsv"
PDIC_PATCHER="$REPO_ROOT/scripts/patch-pdic-factory-return.py"

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
if [[ "$A33X_PDIC_FACTORY_PATCH" == "1" ]] && ! command -v nm >/dev/null 2>&1; then
    echo "nm is required for the recovery-only PDIC factory patch" >&2
    exit 1
fi
if [[ "$A33X_PDIC_FACTORY_PATCH" != "0" && "$A33X_PDIC_FACTORY_PATCH" != "1" ]]; then
    echo "A33X_PDIC_FACTORY_PATCH must be 0 or 1" >&2
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

if [[ "$A33X_PDIC_FACTORY_PATCH" == "1" && ! -f "$PDIC_PATCHER" ]]; then
    echo "Missing required patcher: $PDIC_PATCHER" >&2
    exit 1
fi

rm -rf "$STAGE"
mkdir -p "$STAGE/usr/lib/modules/$KREL"

cp -a "$MODULE_SOURCE/." "$STAGE/usr/lib/modules/$KREL/"
MODULE_ROOT="$STAGE/usr/lib/modules/$KREL"

if [[ "$A33X_PDIC_FACTORY_PATCH" == "1" ]]; then
    echo "=== Apply isolated recovery-only PDIC factory patch ==="

    PDIC_MODULE="$(
        find "$MODULE_ROOT" -type f -name 'pdic_notifier_module.ko' -print -quit
    )"
    if [[ -z "$PDIC_MODULE" || ! -f "$PDIC_MODULE" ]]; then
        echo "REFUSING: pdic_notifier_module.ko was not found" >&2
        exit 1
    fi

    caller_report="$PORT_ROOT/build/u0c-pdic-factory-callers.txt"
    : > "$caller_report"
    while IFS= read -r module; do
        if nm -u "$module" 2>/dev/null |
            grep -qw check_factory_mode_boot
        then
            modinfo -F name "$module" >> "$caller_report"
        fi
    done < <(find "$MODULE_ROOT" -type f -name '*.ko' | sort)

    sort -u -o "$caller_report" "$caller_report"
    if [[ "$(cat "$caller_report")" != "usb_typec_manager" ]]; then
        echo "REFUSING: unexpected callers of check_factory_mode_boot" >&2
        cat "$caller_report" >&2
        exit 1
    fi

    before_name="$(modinfo -F name "$PDIC_MODULE")"
    before_vermagic="$(modinfo -F vermagic "$PDIC_MODULE")"
    before_depends="$(modinfo -F depends "$PDIC_MODULE")"

    # Keep the temporary output ending in .ko. kmod's modinfo treats paths
    # with other suffixes (for example .ko.patched) as module names instead
    # of ELF module files and refuses them before metadata comparison.
    patched_module="${PDIC_MODULE%.ko}.patched.ko"
    rm -f "$patched_module"
    python3 "$PDIC_PATCHER" \
        --module "$PDIC_MODULE" \
        --output "$patched_module" \
        --report "$PORT_ROOT/build/u0c-pdic-factory-patch.txt"

    after_name="$(modinfo -F name "$patched_module")"
    after_vermagic="$(modinfo -F vermagic "$patched_module")"
    after_depends="$(modinfo -F depends "$patched_module")"

    if [[ "$before_name" != "$after_name" \
        || "$before_vermagic" != "$after_vermagic" \
        || "$before_depends" != "$after_depends" ]]
    then
        echo "REFUSING: module metadata changed unexpectedly after patch" >&2
        exit 1
    fi

    mv "$patched_module" "$PDIC_MODULE"
    python3 "$PDIC_PATCHER" \
        --module "$PDIC_MODULE" \
        --verify-patched >/dev/null

    echo "PDIC factory patch verified: $PDIC_MODULE"
    echo "Only binary caller: usb_typec_manager"
fi

echo "=== Generate module dependency indexes ==="
depmod \
    -b "$STAGE" \
    -m /usr/lib/modules \
    "$KREL"

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
if [[ "$A33X_PDIC_FACTORY_PATCH" == "1" ]]; then
    echo "Recovery-only PDIC factory return patch: enabled"
else
    echo "Recovery-only PDIC factory return patch: disabled"
fi
echo "modules.tar.gz: $KPKG/modules.tar.gz"
echo "modules-initfs: $DPKG/modules-initfs"
echo "report: $PORT_ROOT/build/modules-initfs-safe.report.txt"
echo
echo "Next: run pmbootstrap checksum for both A33 packages."

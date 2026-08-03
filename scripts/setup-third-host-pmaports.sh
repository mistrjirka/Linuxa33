#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PMBOOTSTRAP_WORK="${PMBOOTSTRAP_WORK:-$HOME/.local/var/pmbootstrap}"
PMAPORTS="${PMAPORTS:-$PMBOOTSTRAP_WORK/cache_git/pmaports}"
PMAPORTS_URL="${PMAPORTS_URL:-https://gitlab.postmarketos.org/postmarketOS/pmaports.git}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_PORTS="$REPO_ROOT/pmaports"

for command in git rsync mkdir; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

custom_aports=(
    device/downstream/linux-samsung-a33x/APKBUILD
    device/downstream/device-samsung-a33x/APKBUILD
    main/postmarketos-mkinitfs-hook-a33x-watchdog/APKBUILD
    main/postmarketos-mkinitfs-hook-a33x-usbpd/APKBUILD
    main/postmarketos-mkinitfs-hook-a33x-muic-switch/APKBUILD
    main/postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic/APKBUILD
    main/postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic/APKBUILD
)

for relative in "${custom_aports[@]}"; do
    required="$LOCAL_PORTS/$relative"
    if [[ ! -f "$required" ]]; then
        echo "Missing Linuxa33 custom aport: $required" >&2
        exit 1
    fi
done

mkdir -p "$(dirname "$PMAPORTS")"

if [[ -d "$PMAPORTS/.git" ]]; then
    actual_origin="$(git -C "$PMAPORTS" remote get-url origin 2>/dev/null || true)"
    if [[ "$actual_origin" != "$PMAPORTS_URL" ]]; then
        echo "REFUSING: existing pmaports checkout has unexpected origin" >&2
        echo "expected=$PMAPORTS_URL" >&2
        echo "actual=$actual_origin" >&2
        exit 1
    fi
    echo "Existing official pmaports checkout found: $PMAPORTS"
elif [[ -e "$PMAPORTS" ]]; then
    echo "REFUSING: pmaports path exists but is not a Git checkout: $PMAPORTS" >&2
    exit 1
else
    echo "=== Clone official pmaports ==="
    git clone --depth=1 "$PMAPORTS_URL" "$PMAPORTS"
fi

echo "=== Overlay Linuxa33 custom aports and local prebuilt payloads ==="
# Do not use --delete. Locally generated files such as the guarded
# device-samsung-a33x/modules-initfs are intentionally retained.
rsync -a "$LOCAL_PORTS/" "$PMAPORTS/"

for relative in "${custom_aports[@]}"; do
    required="$PMAPORTS/$relative"
    if [[ ! -f "$required" ]]; then
        echo "REFUSING: pmaports overlay is incomplete: $required" >&2
        exit 1
    fi
done

for required in \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/Image" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/samsung-a33x.dtb" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/recovery_dtbo"
do
    if [[ ! -f "$required" ]]; then
        echo "REFUSING: pmaports overlay is missing local prebuilt payload: $required" >&2
        exit 1
    fi
done

cat <<EOF

Third-host pmaports checkout is ready:
  $PMAPORTS

Confirmed U0g packages are present:
  postmarketos-mkinitfs-hook-a33x-watchdog
  postmarketos-mkinitfs-hook-a33x-usbpd
  postmarketos-mkinitfs-hook-a33x-muic-switch-dynamic
  postmarketos-mkinitfs-hook-a33x-muic-persist-dynamic

On a fresh host, run:
  pmbootstrap init

Select the existing custom device samsung-a33x and the edge channel.
Do not run pmbootstrap pull after this overlay without re-running this script,
because a pull/update may require the custom aports to be overlaid again.
EOF

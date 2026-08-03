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

for required in \
    "$LOCAL_PORTS/device/downstream/linux-samsung-a33x/APKBUILD" \
    "$LOCAL_PORTS/device/downstream/device-samsung-a33x/APKBUILD" \
    "$LOCAL_PORTS/main/postmarketos-mkinitfs-hook-a33x-watchdog/APKBUILD" \
    "$LOCAL_PORTS/main/postmarketos-mkinitfs-hook-a33x-usbpd/APKBUILD" \
    "$LOCAL_PORTS/main/postmarketos-mkinitfs-hook-a33x-muic-switch/APKBUILD"
do
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
rsync -a "$LOCAL_PORTS/" "$PMAPORTS/"

for required in \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/APKBUILD" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/Image" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/samsung-a33x.dtb" \
    "$PMAPORTS/device/downstream/linux-samsung-a33x/recovery_dtbo" \
    "$PMAPORTS/device/downstream/device-samsung-a33x/APKBUILD" \
    "$PMAPORTS/main/postmarketos-mkinitfs-hook-a33x-watchdog/APKBUILD" \
    "$PMAPORTS/main/postmarketos-mkinitfs-hook-a33x-usbpd/APKBUILD" \
    "$PMAPORTS/main/postmarketos-mkinitfs-hook-a33x-muic-switch/APKBUILD"
do
    if [[ ! -f "$required" ]]; then
        echo "REFUSING: pmaports overlay is incomplete: $required" >&2
        exit 1
    fi
done

cat <<EOF

Third-host pmaports checkout is ready:
  $PMAPORTS

Now run:
  pmbootstrap init

Select the existing custom device samsung-a33x and the edge channel.
Do not run pmbootstrap pull after this overlay without re-running this script,
because a pull/update may require the custom aports to be overlaid again.
EOF

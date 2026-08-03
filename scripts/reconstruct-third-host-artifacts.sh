#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
KREL="${KREL:-5.10.66-Gabriel260BR-TWRP-ga0103aac9499}"
EXPECTED_TWRP_SHA256="${EXPECTED_TWRP_SHA256:-414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e}"
EXPECTED_TWRP_SIZE="${EXPECTED_TWRP_SIZE:-100663296}"
EXPECTED_TYPEC_ORIGINAL_SHA256="${EXPECTED_TYPEC_ORIGINAL_SHA256:-3a2d75c5e460d2aa0196ac363cddff1cf85d29507d572110008cfccc3e570ea7}"
EXPECTED_TYPEC_PATCHED_SHA256="${EXPECTED_TYPEC_PATCHED_SHA256:-de92f9dc0d29d671bd20f42ad01688e0584eb8e43f6826ff2643e0767c814641}"
EXPECTED_PDIC_SHA256="${EXPECTED_PDIC_SHA256:-5442a4cf5d4f12f394e5c3d4f5f01785929427fa71101731ea16bd00d0840161}"
EXPECTED_MODULE_FILES="${EXPECTED_MODULE_FILES:-315}"
EXPECTED_KERNEL_SIZE="${EXPECTED_KERNEL_SIZE:-31461888}"
EXPECTED_DTB_SIZE="${EXPECTED_DTB_SIZE:-241292}"
EXPECTED_RECOVERY_DTBO_SIZE="${EXPECTED_RECOVERY_DTBO_SIZE:-992404}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PATCHER="$REPO_ROOT/scripts/patch-typec-muic-none-mask.py"

RESCUE_TAR="$PORT_ROOT/build/rescue/twrp-a33x-restore.img.tar"
STAGED_MODULES="$PORT_ROOT/build/modules-stage-safe/usr/lib/modules/$KREL"
TWRP="$PORT_ROOT/reference/twrp/recovery.img"
UNPACKED="$PORT_ROOT/unpacked/twrp"
MODULE_SOURCE="$PORT_ROOT/unpacked/twrp-root/lib/modules"
MKBOOTIMG_REPO="$PORT_ROOT/aosp-mkbootimg"
AVB_REPO="$PORT_ROOT/aosp-avb"
KPKG_SOURCE="$REPO_ROOT/pmaports/device/downstream/linux-samsung-a33x"
REPORT_DIR="$PORT_ROOT/build/third-host-reconstruction"
REPORT="$REPORT_DIR/manifest.txt"

for command in git python3 sha256sum stat find cp mv rm mkdir install; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

for required in \
    "$RESCUE_TAR" \
    "$STAGED_MODULES" \
    "$STAGED_MODULES/modules.load.recovery" \
    "$PATCHER" \
    "$KPKG_SOURCE/APKBUILD"
do
    if [[ ! -e "$required" ]]; then
        echo "Missing required copied/repository artifact: $required" >&2
        exit 1
    fi
done

mkdir -p "$REPORT_DIR" "$(dirname "$TWRP")"

recover_twrp() {
    if [[ -f "$TWRP" ]]; then
        local size hash
        size="$(stat -Lc '%s' "$TWRP")"
        hash="$(sha256sum "$TWRP" | awk '{print $1}')"
        if [[ "$size" == "$EXPECTED_TWRP_SIZE" && "$hash" == "$EXPECTED_TWRP_SHA256" ]]; then
            echo "Known-good TWRP already present: $TWRP"
            return
        fi
        echo "REFUSING: existing TWRP reference has unexpected identity" >&2
        echo "size=$size sha256=$hash" >&2
        exit 1
    fi

    echo "=== Recover exact TWRP from Odin rescue tar ==="
    python3 - "$RESCUE_TAR" "$TWRP" "$EXPECTED_TWRP_SIZE" "$EXPECTED_TWRP_SHA256" <<'PY'
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import sys
import tarfile

archive = Path(sys.argv[1])
destination = Path(sys.argv[2])
expected_size = int(sys.argv[3])
expected_hash = sys.argv[4]

matches: list[tarfile.TarInfo] = []
with tarfile.open(archive, "r:*") as tf:
    for member in tf.getmembers():
        if not member.isfile() or member.size != expected_size:
            continue
        stream = tf.extractfile(member)
        if stream is None:
            continue
        digest = hashlib.sha256()
        while True:
            chunk = stream.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() == expected_hash:
            matches.append(member)

if len(matches) != 1:
    raise SystemExit(
        f"expected exactly one TWRP payload in {archive}, found {len(matches)}"
    )

member = matches[0]
destination.parent.mkdir(parents=True, exist_ok=True)
temporary = destination.with_name(destination.name + ".tmp")
with tarfile.open(archive, "r:*") as tf:
    stream = tf.extractfile(member)
    if stream is None:
        raise SystemExit("failed to reopen matched TWRP payload")
    with temporary.open("wb") as output:
        shutil.copyfileobj(stream, output, length=1024 * 1024)
os.replace(temporary, destination)
print(f"restored_member={member.name}")
PY

    test "$(stat -Lc '%s' "$TWRP")" = "$EXPECTED_TWRP_SIZE"
    test "$(sha256sum "$TWRP" | awk '{print $1}')" = "$EXPECTED_TWRP_SHA256"
    echo "TWRP recovery restored and verified: $TWRP"
}

clone_aosp_tool() {
    local url="$1"
    local destination="$2"
    local label="$3"

    if [[ -d "$destination/.git" ]]; then
        echo "=== Refresh $label ==="
        git -C "$destination" fetch --depth=1 origin main
        git -C "$destination" checkout --detach FETCH_HEAD
    elif [[ -e "$destination" ]]; then
        echo "REFUSING: $destination exists but is not a Git checkout" >&2
        exit 1
    else
        echo "=== Clone $label ==="
        git clone --depth=1 --branch main "$url" "$destination"
    fi
}

recover_original_modules() {
    echo "=== Reconstruct exact original TWRP module source tree ==="

    local staged_count tmp typec pdic typec_hash pdic_hash
    staged_count="$(find "$STAGED_MODULES" -type f -name '*.ko' | wc -l)"
    if [[ "$staged_count" != "$EXPECTED_MODULE_FILES" ]]; then
        echo "REFUSING: staged tree has $staged_count .ko files, expected $EXPECTED_MODULE_FILES" >&2
        exit 1
    fi

    tmp="$PORT_ROOT/unpacked/twrp-root/lib/modules.reconstructing"
    rm -rf "$tmp"
    mkdir -p "$(dirname "$tmp")"
    cp -a "$STAGED_MODULES" "$tmp"

    typec="$(find "$tmp" -type f -name 'usb_typec_manager.ko' -print -quit)"
    pdic="$(find "$tmp" -type f -name 'pdic_notifier_module.ko' -print -quit)"
    for required in "$typec" "$pdic" "$tmp/modules.load.recovery"; do
        if [[ -z "$required" || ! -f "$required" ]]; then
            echo "REFUSING: reconstructed source is missing $required" >&2
            exit 1
        fi
    done

    typec_hash="$(sha256sum "$typec" | awk '{print $1}')"
    case "$typec_hash" in
        "$EXPECTED_TYPEC_ORIGINAL_SHA256")
            python3 "$PATCHER" --module "$typec" --verify-original >/dev/null
            echo "Type-C module was already the exact original"
            ;;
        "$EXPECTED_TYPEC_PATCHED_SHA256")
            restored="${typec%.ko}.restored.ko"
            python3 "$PATCHER" \
                --module "$typec" \
                --restore-original \
                --output "$restored" \
                --report "$REPORT_DIR/typec-restore.txt"
            mv "$restored" "$typec"
            python3 "$PATCHER" --module "$typec" --verify-original >/dev/null
            echo "Type-C module restored from exact U0d one-instruction patch"
            ;;
        *)
            echo "REFUSING: staged Type-C module has unknown SHA256: $typec_hash" >&2
            exit 1
            ;;
    esac

    pdic_hash="$(sha256sum "$pdic" | awk '{print $1}')"
    if [[ "$pdic_hash" != "$EXPECTED_PDIC_SHA256" ]]; then
        echo "REFUSING: staged PDIC module is not the original binary: $pdic_hash" >&2
        exit 1
    fi

    if [[ "$(find "$tmp" -type f -name '*.ko' | wc -l)" != "$EXPECTED_MODULE_FILES" ]]; then
        echo "REFUSING: reconstructed module count changed" >&2
        exit 1
    fi

    rm -rf "$MODULE_SOURCE"
    mv "$tmp" "$MODULE_SOURCE"

    test "$(sha256sum "$MODULE_SOURCE/usb_typec_manager.ko" | awk '{print $1}')" = "$EXPECTED_TYPEC_ORIGINAL_SHA256"
    test "$(sha256sum "$MODULE_SOURCE/pdic_notifier_module.ko" | awk '{print $1}')" = "$EXPECTED_PDIC_SHA256"
    echo "Original module source restored: $MODULE_SOURCE"
}

extract_twrp_components() {
    echo "=== Extract TWRP kernel, DTB and recovery-DTBO ==="
    rm -rf "$UNPACKED"
    mkdir -p "$UNPACKED"
    python3 "$MKBOOTIMG_REPO/unpack_bootimg.py" \
        --boot_img "$TWRP" \
        --out "$UNPACKED" >/dev/null

    for required in kernel dtb recovery_dtbo; do
        if [[ ! -f "$UNPACKED/$required" ]]; then
            echo "REFUSING: unpack_bootimg did not produce $UNPACKED/$required" >&2
            exit 1
        fi
    done

    test "$(stat -Lc '%s' "$UNPACKED/kernel")" = "$EXPECTED_KERNEL_SIZE"
    test "$(stat -Lc '%s' "$UNPACKED/dtb")" = "$EXPECTED_DTB_SIZE"
    test "$(stat -Lc '%s' "$UNPACKED/recovery_dtbo")" = "$EXPECTED_RECOVERY_DTBO_SIZE"

    install -m 0644 "$UNPACKED/kernel" "$KPKG_SOURCE/Image"
    install -m 0644 "$UNPACKED/dtb" "$KPKG_SOURCE/samsung-a33x.dtb"
    install -m 0644 "$UNPACKED/recovery_dtbo" "$KPKG_SOURCE/recovery_dtbo"

    echo "Kernel package prebuilt payloads restored under: $KPKG_SOURCE"
}

recover_twrp
clone_aosp_tool \
    https://android.googlesource.com/platform/system/tools/mkbootimg \
    "$MKBOOTIMG_REPO" \
    "AOSP mkbootimg"
clone_aosp_tool \
    https://android.googlesource.com/platform/external/avb \
    "$AVB_REPO" \
    "AOSP AVB"

for required in \
    "$MKBOOTIMG_REPO/mkbootimg.py" \
    "$MKBOOTIMG_REPO/unpack_bootimg.py" \
    "$AVB_REPO/avbtool.py"
do
    if [[ ! -f "$required" ]]; then
        echo "REFUSING: cloned AOSP tool is missing: $required" >&2
        exit 1
    fi
done

recover_original_modules
extract_twrp_components

{
    echo "status=complete"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "twrp=$TWRP"
    echo "twrp_size=$(stat -Lc '%s' "$TWRP")"
    echo "twrp_sha256=$(sha256sum "$TWRP" | awk '{print $1}')"
    echo "mkbootimg_commit=$(git -C "$MKBOOTIMG_REPO" rev-parse HEAD)"
    echo "avb_commit=$(git -C "$AVB_REPO" rev-parse HEAD)"
    echo "kernel_size=$(stat -Lc '%s' "$UNPACKED/kernel")"
    echo "kernel_sha256=$(sha256sum "$UNPACKED/kernel" | awk '{print $1}')"
    echo "dtb_size=$(stat -Lc '%s' "$UNPACKED/dtb")"
    echo "dtb_sha256=$(sha256sum "$UNPACKED/dtb" | awk '{print $1}')"
    echo "recovery_dtbo_size=$(stat -Lc '%s' "$UNPACKED/recovery_dtbo")"
    echo "recovery_dtbo_sha256=$(sha256sum "$UNPACKED/recovery_dtbo" | awk '{print $1}')"
    echo "module_source=$MODULE_SOURCE"
    echo "module_files=$(find "$MODULE_SOURCE" -type f -name '*.ko' | wc -l)"
    echo "typec_original_sha256=$(sha256sum "$MODULE_SOURCE/usb_typec_manager.ko" | awk '{print $1}')"
    echo "pdic_original_sha256=$(sha256sum "$MODULE_SOURCE/pdic_notifier_module.ko" | awk '{print $1}')"
} | tee "$REPORT"

echo
echo "Third-host local artifacts reconstructed successfully."
echo "Manifest: $REPORT"
echo "Next: initialize pmaports/pmbootstrap and rebuild the A33 rootfs chroot."

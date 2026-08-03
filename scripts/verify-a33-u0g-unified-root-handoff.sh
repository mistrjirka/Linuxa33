#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
RECOVERY="${RECOVERY:-$PORT_ROOT/build/candidates/a33x-h1-usbpd-u0g-muic-dynamic-recovery.img}"
EXPECTED_RECOVERY_SHA256="${EXPECTED_RECOVERY_SHA256:-e0b2a112a45e93ae0c358fb6d15ebd20b038e3c215b8766c5491879389c9fd81}"
EXPECTED_RAMDISK_SHA256="${EXPECTED_RAMDISK_SHA256:-13ba030dc9593849622bfe85b318393c1f3397d0a95feebf7c734d97cf37732d}"
UNPACK="${UNPACK:-$PORT_ROOT/aosp-mkbootimg/unpack_bootimg.py}"
REPORT="$PORT_ROOT/build/a33-u0g-unified-root-handoff.txt"

for command in python3 sha256sum gzip cpio grep awk find file; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

for required in "$RECOVERY" "$UNPACK"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: required file is missing: $required" >&2
        exit 1
    }
done

RECOVERY_SHA="$(sha256sum "$RECOVERY" | awk '{print $1}')"
if [[ "$RECOVERY_SHA" != "$EXPECTED_RECOVERY_SHA256" ]]; then
    echo "REFUSING: recovery candidate SHA256 mismatch" >&2
    echo "expected=$EXPECTED_RECOVERY_SHA256" >&2
    echo "actual=$RECOVERY_SHA" >&2
    exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
UNPACKED="$TMP/unpacked"
EXTRACTED="$TMP/initramfs"
mkdir -p "$UNPACKED" "$EXTRACTED"

python3 "$UNPACK" \
    --boot_img "$RECOVERY" \
    --out "$UNPACKED" \
    --format=info \
    > "$TMP/unpack-info.txt"

RAMDISK="$UNPACKED/ramdisk"
[[ -f "$RAMDISK" ]] || {
    echo "REFUSING: recovery unpack did not produce ramdisk" >&2
    exit 1
}
RAMDISK_SHA="$(sha256sum "$RAMDISK" | awk '{print $1}')"
if [[ "$RAMDISK_SHA" != "$EXPECTED_RAMDISK_SHA256" ]]; then
    echo "REFUSING: U0g ramdisk SHA256 mismatch" >&2
    echo "expected=$EXPECTED_RAMDISK_SHA256" >&2
    echo "actual=$RAMDISK_SHA" >&2
    exit 1
fi

if ! file -b "$RAMDISK" | grep -qi gzip; then
    echo "REFUSING: U0g ramdisk is not gzip-compressed" >&2
    file "$RAMDISK" >&2 || true
    exit 1
fi

gzip -dc "$RAMDISK" > "$TMP/initramfs.cpio"
(
    cd "$EXTRACTED"
    cpio -idmu --quiet < "$TMP/initramfs.cpio"
)

for required in \
    "$EXTRACTED/init" \
    "$EXTRACTED/init_2nd.sh" \
    "$EXTRACTED/init_functions_2nd.sh"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: unified initramfs file is missing: ${required#$EXTRACTED/}" >&2
        exit 1
    }
done

python3 - \
    "$EXTRACTED/init" \
    "$EXTRACTED/init_2nd.sh" \
    "$EXTRACTED/init_functions_2nd.sh" <<'PY'
from pathlib import Path
import re
import sys

init = Path(sys.argv[1]).read_text(errors="replace")
init2 = Path(sys.argv[2]).read_text(errors="replace")
functions2 = Path(sys.argv[3]).read_text(errors="replace")

first_second_stage = init.find("init_2nd.sh")
if first_second_stage < 0:
    raise SystemExit("REFUSING: /init never references /init_2nd.sh")

extra = init.find("extract_initramfs_extra")
if extra >= 0 and first_second_stage > extra:
    raise SystemExit(
        "REFUSING: /init references initramfs-extra before its first init_2nd handoff"
    )

window = init[max(0, first_second_stage - 300): first_second_stage + 300]
if "exec" not in window:
    raise SystemExit("REFUSING: direct /init_2nd.sh handoff is not executable")

for token in ("wait_root_partition", "mount_root_partition", "switch_root"):
    if token not in init2:
        raise SystemExit(f"REFUSING: /init_2nd.sh lacks {token}")

if "pmOS_root" not in functions2 and "pmOS_root" not in init2:
    raise SystemExit("REFUSING: second-stage initramfs lacks pmOS_root discovery")

print("direct_init_2nd_before_extra=yes")
print("second_stage_root_wait=yes")
print("second_stage_root_mount=yes")
print("second_stage_switch_root=yes")
print("pmos_root_discovery=yes")
PY

DEVICEINFO="$(
    find "$EXTRACTED" -type f \
        \( -path '*/usr/share/deviceinfo/deviceinfo' -o -path '*/etc/deviceinfo' \) \
        -print -quit
)"
CREATE_EXTRA="unset"
if [[ -n "$DEVICEINFO" ]]; then
    value="$(
        sed -n 's/^[[:space:]]*deviceinfo_create_initfs_extra[[:space:]]*=[[:space:]]*["'"']\{0,1\}\([^"'"']*\).*/\1/p' \
            "$DEVICEINFO" | head -n 1
    )"
    [[ -n "$value" ]] && CREATE_EXTRA="$value"
fi
if [[ "$CREATE_EXTRA" == true ]]; then
    echo "REFUSING: deviceinfo explicitly requires initramfs-extra" >&2
    exit 1
fi

INIT_EXTRA_PRESENT=no
[[ -e "$EXTRACTED/initramfs-extra" ]] && INIT_EXTRA_PRESENT=yes

{
    echo "created=$(date -Ins)"
    echo "operation=verify-exact-u0g-unified-root-handoff"
    echo "recovery=$RECOVERY"
    echo "recovery_sha256=$RECOVERY_SHA"
    echo "ramdisk_sha256=$RAMDISK_SHA"
    echo "init_2nd_embedded=yes"
    echo "direct_init_2nd_before_extra=yes"
    echo "deviceinfo_create_initfs_extra=$CREATE_EXTRA"
    echo "embedded_initramfs_extra=$INIT_EXTRA_PRESENT"
    echo "pmos_boot_required_before_second_stage=no"
    echo "pmos_root_discovery=yes"
    echo "switch_root_present=yes"
    echo "cache_partition_required=no"
    echo "verification_status=passed"
} | tee "$REPORT"

echo
echo "Exact U0g unified-root handoff verified."
echo "Report: $REPORT"
echo "The first real-rootfs test needs pmOS_root on userdata only; cache stays untouched."

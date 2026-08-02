#!/usr/bin/env bash
set -euo pipefail

ROOT="${ROOT:-$HOME/a33-port}"

TWRP="$ROOT/reference/twrp/recovery.img"
PMOS_INITRAMFS="$ROOT/export-debug/initramfs"

UNPACK="$ROOT/aosp-mkbootimg/unpack_bootimg.py"
MKBOOTIMG="$ROOT/aosp-mkbootimg/mkbootimg.py"
AVBTOOL="$ROOT/aosp-avb/avbtool.py"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_REPO="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$SCRIPT_REPO/config/modules-initfs-blocklist.glob" ]]; then
    LINUXA33_REPO="${LINUXA33_REPO:-$SCRIPT_REPO}"
else
    LINUXA33_REPO="${LINUXA33_REPO:-$HOME/Linuxa33}"
fi

SAFETY_CHECKER="$LINUXA33_REPO/scripts/verify-initramfs-safety.py"
SAFETY_BLOCKLIST="$LINUXA33_REPO/config/modules-initfs-blocklist.glob"
SAFETY_MAX_MODULES="${SAFETY_MAX_MODULES:-128}"
ACTIVATION_CHECKER="$LINUXA33_REPO/scripts/verify-module-activation.py"
ACTIVATION_CONTRACTS="$LINUXA33_REPO/config/module-activation-contracts.tsv"

OUT="$ROOT/build/pmos-debug-recovery"
LAYOUT="$OUT/twrp-layout"
CHECK="$OUT/final-unpacked"
ARGS0="$OUT/mkbootimg.args0"

ROUNDTRIP="$OUT/twrp-roundtrip.raw.img"
TWRP_PREFIX="$OUT/twrp-original-prefix.img"
TRAILER="$OUT/twrp-trailer.bin"

RAW="$OUT/pmos-debug-recovery.raw.img"
FINAL="$OUT/recovery.img"

KEYDIR="$ROOT/build/keys"
KEY="$KEYDIR/a33x-recovery-test-rsa4096.pem"

PARTITION_SIZE=100663296
SALT="7c55e8f984b83d022e379d25da00a2deb2e248b57ee7c31224e8805f80f2b2ed"

for required_file in \
    "$TWRP" \
    "$PMOS_INITRAMFS" \
    "$UNPACK" \
    "$MKBOOTIMG" \
    "$AVBTOOL" \
    "$SAFETY_CHECKER" \
    "$SAFETY_BLOCKLIST" \
    "$ACTIVATION_CHECKER" \
    "$ACTIVATION_CONTRACTS"
do
    if [[ ! -f "$required_file" ]]; then
        echo "Missing required file: $required_file" >&2
        exit 1
    fi
done

echo "=== Fail-closed initramfs safety check ==="
python3 "$SAFETY_CHECKER" \
    --initramfs "$PMOS_INITRAMFS" \
    --blocklist "$SAFETY_BLOCKLIST" \
    --max-modules "$SAFETY_MAX_MODULES"

echo
echo "=== Fail-closed module activation check ==="
python3 "$ACTIVATION_CHECKER" \
    --contracts "$ACTIVATION_CONTRACTS" \
    --repo-root "$LINUXA33_REPO" \
    --initramfs "$PMOS_INITRAMFS"

rm -rf "$OUT"
mkdir -p "$LAYOUT" "$CHECK" "$KEYDIR"
chmod 700 "$KEYDIR"

echo
echo "=== Extract exact TWRP layout and arguments ==="

python3 "$UNPACK" \
    --boot_img "$TWRP" \
    --out "$LAYOUT" \
    --format=mkbootimg \
    -0 > "$ARGS0"

mapfile -d '' -t MKBOOTIMG_ARGS < "$ARGS0"

if (( ${#MKBOOTIMG_ARGS[@]} == 0 )); then
    echo "No mkbootimg arguments were extracted" >&2
    exit 1
fi

echo "Extracted ${#MKBOOTIMG_ARGS[@]} mkbootimg arguments"

echo
echo "=== Verify TWRP reconstruction ==="

python3 "$MKBOOTIMG" \
    "${MKBOOTIMG_ARGS[@]}" \
    --output "$ROUNDTRIP"

ORIGINAL_SIZE="$(
    python3 "$AVBTOOL" info_image --image "$TWRP" |
        awk '/Original image size:/ { print $4; exit }'
)"

ROUNDTRIP_SIZE="$(stat -c '%s' "$ROUNDTRIP")"

if [[ ! "$ORIGINAL_SIZE" =~ ^[0-9]+$ ]]; then
    echo "Could not parse original TWRP image size" >&2
    exit 1
fi

if (( ROUNDTRIP_SIZE > ORIGINAL_SIZE )); then
    echo "Reconstructed image is larger than AVB original image" >&2
    exit 1
fi

TRAILER_SIZE=$((ORIGINAL_SIZE - ROUNDTRIP_SIZE))

python3 - \
    "$TWRP" \
    "$TWRP_PREFIX" \
    "$TRAILER" \
    "$ROUNDTRIP_SIZE" \
    "$TRAILER_SIZE" <<'PY'
from pathlib import Path
import sys

source = Path(sys.argv[1])
prefix_output = Path(sys.argv[2])
trailer_output = Path(sys.argv[3])
prefix_size = int(sys.argv[4])
trailer_size = int(sys.argv[5])

with source.open("rb") as stream:
    prefix = stream.read(prefix_size)
    trailer = stream.read(trailer_size)

if len(prefix) != prefix_size:
    raise SystemExit("Failed to read complete TWRP prefix")

if len(trailer) != trailer_size:
    raise SystemExit("Failed to read complete TWRP trailer")

prefix_output.write_bytes(prefix)
trailer_output.write_bytes(trailer)
PY

cmp "$ROUNDTRIP" "$TWRP_PREFIX"

echo "TWRP mkbootimg reconstruction: exact"
echo "Normal mkbootimg size:         $ROUNDTRIP_SIZE bytes"
echo "Additional trailer size:      $TRAILER_SIZE bytes"

if (( TRAILER_SIZE > 0 )); then
    echo
    echo "=== Exact trailer bytes ==="
    xxd -g1 "$TRAILER"
    echo
    echo "=== Trailer strings ==="
    strings "$TRAILER" || true
fi

echo
echo "=== Build postmarketOS recovery payload ==="

# Replace only the ramdisk. Keep the proven TWRP kernel, DTB,
# recovery-DTBO, header values, addresses and command line.
cp -L "$PMOS_INITRAMFS" "$LAYOUT/ramdisk"

python3 "$MKBOOTIMG" \
    "${MKBOOTIMG_ARGS[@]}" \
    --output "$RAW"

if (( TRAILER_SIZE > 0 )); then
    cat "$TRAILER" >> "$RAW"
fi

RAW_SIZE="$(stat -c '%s' "$RAW")"

MAX_RAW_SIZE="$(
    python3 "$AVBTOOL" add_hash_footer \
        --partition_name recovery \
        --partition_size "$PARTITION_SIZE" \
        --calc_max_image_size
)"

echo "Raw image size:         $RAW_SIZE bytes"
echo "Maximum AVB image size: $MAX_RAW_SIZE bytes"

if (( RAW_SIZE > MAX_RAW_SIZE )); then
    echo "Recovery payload is too large for the partition" >&2
    exit 1
fi

echo
echo "=== Create local AVB test key ==="

if [[ ! -f "$KEY" ]]; then
    openssl genpkey \
        -algorithm RSA \
        -pkeyopt rsa_keygen_bits:4096 \
        -out "$KEY"
    chmod 600 "$KEY"
else
    echo "Reusing existing key: $KEY"
fi

cp "$RAW" "$FINAL"

python3 "$AVBTOOL" add_hash_footer \
    --image "$FINAL" \
    --partition_name recovery \
    --partition_size "$PARTITION_SIZE" \
    --hash_algorithm sha256 \
    --algorithm SHA256_RSA4096 \
    --key "$KEY" \
    --salt "$SALT"

FINAL_SIZE="$(stat -c '%s' "$FINAL")"

if (( FINAL_SIZE != PARTITION_SIZE )); then
    echo "Unexpected final size: $FINAL_SIZE" >&2
    exit 1
fi

echo
echo "=== Verify AVB signature and digest ==="

python3 "$AVBTOOL" verify_image \
    --image "$FINAL" \
    --key "$KEY" |
    tee "$OUT/avb-verify.txt"

python3 "$AVBTOOL" info_image \
    --image "$FINAL" |
    tee "$OUT/avb-info.txt"

echo
echo "=== Re-extract and validate final recovery ==="

rm -rf "$CHECK"
mkdir -p "$CHECK"

python3 "$UNPACK" \
    --boot_img "$FINAL" \
    --out "$CHECK" \
    --format=info |
    tee "$OUT/final-boot-info.txt"

cmp "$CHECK/kernel" "$LAYOUT/kernel"
cmp "$CHECK/ramdisk" "$PMOS_INITRAMFS"
cmp "$CHECK/recovery_dtbo" "$LAYOUT/recovery_dtbo"
cmp "$CHECK/dtb" "$LAYOUT/dtb"

echo
echo "=== FINAL VALIDATION ==="
echo "Initramfs safety gate:  passed"
echo "Module activation gate: passed"
echo "TWRP header/layout:      verified"
echo "TWRP kernel:             unchanged"
echo "TWRP DTB:                unchanged"
echo "TWRP recovery-DTBO:      unchanged"
echo "Samsung trailer:         preserved ($TRAILER_SIZE bytes)"
echo "postmarketOS initramfs:  verified"
echo "AVB footer:              verified"
echo "Final partition size:    $FINAL_SIZE bytes"

sha256sum "$FINAL"

echo
echo "Result:"
echo "$FINAL"

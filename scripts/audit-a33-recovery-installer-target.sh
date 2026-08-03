#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-debug}"
ADB="${ADB:-adb}"
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$PORT_ROOT/build/a33-installer-target-audit-$TIMESTAMP"
ARCHIVE="$OUT.tar.gz"
ZIP_LINK="$EXPORT_DIR/pmos-samsung-a33x.zip"

for command in "$ADB" unzip sha256sum stat file find awk sed grep tar fdisk blkid; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done

mkdir -p "$OUT"/{host,installer,images,twrp}

capture() {
    local output="$1"
    shift
    {
        printf 'command:'
        printf ' %q' "$@"
        printf '\n\n'
        "$@"
    } > "$output" 2>&1 || true
}

if [[ ! -e "$ZIP_LINK" ]]; then
    echo "REFUSING: generated recovery installer link is missing: $ZIP_LINK" >&2
    exit 1
fi

ZIP="$(readlink -f "$ZIP_LINK")"
if [[ ! -f "$ZIP" ]]; then
    echo "REFUSING: generated recovery installer is missing: $ZIP" >&2
    exit 1
fi

{
    echo "created=$(date -Ins)"
    echo "collection_policy=read-only"
    echo "phone_required_mode=TWRP"
    echo "installer_link=$ZIP_LINK"
    echo "installer_resolved=$ZIP"
    echo "secrets_policy=installer_options_password_key_passphrase_fields_redacted"
    echo "no_partition_writes=yes"
    echo "no_mount_rw=yes"
} | tee "$OUT/manifest.txt"

stat -Lc 'installer_size=%s' "$ZIP" > "$OUT/installer/identity.txt"
sha256sum "$ZIP" >> "$OUT/installer/identity.txt"
file "$ZIP" >> "$OUT/installer/identity.txt"
readlink -v "$ZIP_LINK" >> "$OUT/installer/identity.txt" 2>&1 || true

unzip -Z1 "$ZIP" > "$OUT/installer/entries.txt"
unzip -l "$ZIP" > "$OUT/installer/list-long.txt"

extract_member() {
    local member="$1"
    local output="$2"
    if unzip -Z1 "$ZIP" | grep -Fxq "$member"; then
        unzip -p "$ZIP" "$member" > "$output"
    else
        echo "missing_member=$member" > "$output"
    fi
}

extract_member chroot/install_options "$OUT/installer/install_options.raw"
extract_member chroot/bin/pmos_install "$OUT/installer/pmos_install.txt"
extract_member chroot/bin/pmos_install_functions "$OUT/installer/pmos_install_functions.txt"
extract_member chroot/bin/pmos_install_part "$OUT/installer/pmos_install_part.txt"
extract_member META-INF/com/google/android/update-binary "$OUT/installer/update-binary.txt"

# Preserve all non-secret install choices while refusing to archive password,
# passphrase, private-key, or encryption-key values.
awk '
BEGIN { IGNORECASE=1 }
{
    line=$0
    key=line
    sub(/[[:space:]]*=.*/, "", key)
    if (key ~ /(pass(word|wd)?|passphrase|private.*key|secret|token|fde.*key)/) {
        print key "=<redacted>"
    } else {
        print line
    }
}' "$OUT/installer/install_options.raw" > "$OUT/installer/install_options.redacted.txt"
rm -f "$OUT/installer/install_options.raw"

capture "$OUT/installer/install-target-evidence.txt" bash -lc "
    grep -nEi 'recovery_install_partition|install_partition|SYSTEM_PARTLABEL|BOOT_PARTLABEL|pmOS_root|pmOS_boot|system|userdata|data|super|dynamic|subpartition|partition' \
      '$OUT/installer/install_options.redacted.txt' \
      '$OUT/installer/pmos_install.txt' \
      '$OUT/installer/pmos_install_functions.txt' \
      '$OUT/installer/pmos_install_part.txt' \
      | head -n 5000 || true
"

for image in \
    "$EXPORT_DIR/samsung-a33x.img" \
    "$EXPORT_DIR/samsung-a33x-root.img" \
    "$EXPORT_DIR/samsung-a33x-boot.img"; do
    name="$(basename "$image")"
    resolved="$(readlink -f "$image" 2>/dev/null || true)"
    {
        echo "image_link=$image"
        echo "image_resolved=${resolved:-missing}"
        if [[ -f "$resolved" ]]; then
            stat -Lc 'size=%s' "$resolved"
            sha256sum "$resolved"
            file "$resolved"
            blkid -p "$resolved" 2>&1 || true
            fdisk -l "$resolved" 2>&1 || true
        fi
    } > "$OUT/images/$name.txt"
done

# TWRP transport quirk: do not use adb wait-for-device.
echo "=== Wait for TWRP ADB shell ==="
until "$ADB" shell 'echo ADB_OK' 2>/dev/null | grep -q ADB_OK; do
    sleep 1
done

echo "=== Capture read-only phone partition topology ==="
"$ADB" shell sh -s > "$OUT/twrp/partition-topology.txt" <<'SH'
set -u

echo "mode=TWRP"
echo "kernel=$(uname -r 2>/dev/null || true)"
echo "recovery_hash=$(sha256sum /dev/block/by-name/recovery 2>/dev/null | awk 'NR==1 {print $1}')"

echo "=== /dev/block/by-name ==="
ls -la /dev/block/by-name 2>&1 || true

echo "=== resolved by-name entries ==="
for path in /dev/block/by-name/*; do
    [ -e "$path" ] || continue
    printf '%s -> %s\n' "$path" "$(readlink -f "$path" 2>/dev/null || true)"
done

echo "=== proc partitions ==="
cat /proc/partitions 2>&1 || true

echo "=== mounts ==="
cat /proc/mounts 2>&1 || true

echo "=== fstab files ==="
for file in /etc/twrp.fstab /etc/recovery.fstab /system/etc/recovery.fstab; do
    if [ -r "$file" ]; then
        echo "--- $file ---"
        cat "$file"
    fi
done

echo "=== blkid ==="
blkid 2>&1 || true

echo "=== lsblk ==="
lsblk -o NAME,KNAME,MAJ:MIN,SIZE,RO,TYPE,FSTYPE,LABEL,PARTLABEL,UUID,MOUNTPOINTS 2>&1 || \
lsblk 2>&1 || true

echo "=== block sizes for candidate targets ==="
for path in \
    /dev/block/by-name/system \
    /dev/block/by-name/system_a \
    /dev/block/by-name/super \
    /dev/block/by-name/userdata \
    /dev/block/by-name/data \
    /dev/block/by-name/cache \
    /dev/block/by-name/recovery; do
    if [ -b "$path" ]; then
        resolved="$(readlink -f "$path" 2>/dev/null || true)"
        sectors="$(blockdev --getsz "$path" 2>/dev/null || true)"
        bytes="$(blockdev --getsize64 "$path" 2>/dev/null || true)"
        echo "target=$path resolved=${resolved:-unknown} sectors=${sectors:-unknown} bytes=${bytes:-unknown}"
    fi
done

echo "=== dynamic partition tools ==="
for command in lpdump lptools lpmake; do
    command -v "$command" 2>/dev/null || true
done
if command -v lpdump >/dev/null 2>&1; then
    lpdump /dev/block/by-name/super 2>&1 || lpdump 2>&1 || true
fi
SH

# Capture exact target-relevant lines separately for quick review.
grep -aEin \
    'system|super|userdata|data|recovery|pmOS|dynamic|logical|by-name|PARTLABEL|block size|target=' \
    "$OUT/twrp/partition-topology.txt" \
    > "$OUT/twrp/target-lines.txt" || true

{
    echo "installer_sha256=$(sha256sum "$ZIP" | awk '{print $1}')"
    echo "installer_size=$(stat -Lc '%s' "$ZIP")"
    echo "twrp_recovery_sha256=$(awk -F= '$1==\"recovery_hash\" {print $2; exit}' "$OUT/twrp/partition-topology.txt")"
    echo "install_options_present=$(grep -qv '^missing_member=' "$OUT/installer/install_options.redacted.txt" && echo yes || echo no)"
    echo "phone_partition_topology_captured=yes"
    echo "writes_performed=no"
    echo "next_decision=validate-install-target-before-rootfs-write"
} | tee "$OUT/summary.txt"

tar -C "$(dirname "$OUT")" -czf "$ARCHIVE" "$(basename "$OUT")"
sha256sum "$ARCHIVE" | tee "$ARCHIVE.sha256"

echo
echo "A33 installer target audit collected."
echo "Directory: $OUT"
echo "Archive:   $ARCHIVE"
echo "Checksum:  $ARCHIVE.sha256"
echo "Upload the .tar.gz archive only."

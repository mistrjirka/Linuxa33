#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

ROOT="${ROOT:-$HOME/a33-port}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INITRAMFS="$ROOT/export-u0h-root-node/initramfs"
U0H_REPORT="$ROOT/build/u0h-userdata-root-node.txt"
U0G_REPORT="$ROOT/build/u0g-muic-dynamic.txt"
OUT="$ROOT/build/pmos-debug-recovery-u0h"
CANDIDATE="$ROOT/build/candidates/a33x-h1-usbpd-u0h-userdata-root-node-recovery.img"
MANIFEST="$ROOT/build/candidates/a33x-h1-usbpd-u0h-userdata-root-node-manifest.txt"

for command in gzip cpio sha256sum python3 stat cp grep awk find file; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$INITRAMFS" "$U0H_REPORT" "$U0G_REPORT" \
    "$SCRIPT_DIR/make-pmos-debug-recovery.sh"; do
    [[ -e "$required" ]] || {
        echo "Missing required U0h input: $required" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}
verify_sha() {
    local label="$1" path="$2" expected="$3" actual
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING U0h: $label SHA256 mismatch" >&2
        echo "expected=$expected actual=$actual path=$path" >&2
        exit 1
    fi
}

[[ "$(value "$U0H_REPORT" preparation_status)" = passed ]] || {
    echo "REFUSING U0h: preparation report did not pass" >&2
    exit 1
}
verify_sha initramfs "$INITRAMFS" "$(value "$U0H_REPORT" initramfs_sha256)"

EXTRACT="$(mktemp -d)"
cleanup() { rm -rf "$EXTRACT"; }
trap cleanup EXIT
gzip -dc "$INITRAMFS" > "$EXTRACT/initramfs.cpio"
(
    cd "$EXTRACT"
    cpio -idmu --quiet < initramfs.cpio
)
rm -f "$EXTRACT/initramfs.cpio"

for required in \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    hooks/05-a33x-userdata-root-node.sh \
    usr/libexec/a33x-muic-switch-dynamic \
    init init_functions.sh init_2nd.sh init_functions_2nd.sh sysroot; do
    [[ -e "$EXTRACT/$required" ]] || {
        echo "REFUSING U0h: initramfs is missing $required" >&2
        exit 1
    }
done

verify_sha helper-retained "$EXTRACT/usr/libexec/a33x-muic-switch-dynamic" \
    "$(value "$U0G_REPORT" dynamic_helper_sha256)"
verify_sha hook03-retained "$EXTRACT/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook03_sha256)"
verify_sha hook04-retained "$EXTRACT/hooks/04-a33x-muic-persist-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook04_sha256)"
verify_sha hook05-new "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" \
    "$(value "$U0H_REPORT" root_node_hook_sha256)"

module_count="$(find "$EXTRACT/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
[[ "$module_count" = "$EXPECTED_MODULE_COUNT" ]] || {
    echo "REFUSING U0h: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
}

for token in \
    'root_name=sda36' \
    'expected_sectors=223125504' \
    'expected_label=pmOS_root' \
    'metadata_relative=a33x-bringup/u0h-root-node-result.txt'; do
    grep -Fq "$token" "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" || {
        echo "REFUSING U0h: embedded root-node contract is missing: $token" >&2
        exit 1
    }
done
if grep -Eq 'dd .*of=/dev/(block/)?sda36|mkfs|wipefs' \
    "$EXTRACT/hooks/05-a33x-userdata-root-node.sh"; then
    echo "REFUSING U0h: embedded root-node hook contains a destructive operation" >&2
    exit 1
fi

cleanup
trap - EXIT
rm -rf "$OUT"
env \
    ROOT="$ROOT" \
    OUT="$OUT" \
    PMOS_INITRAMFS="$INITRAMFS" \
    EXTRA_KERNEL_CMDLINE="" \
    bash "$SCRIPT_DIR/make-pmos-debug-recovery.sh"

SOURCE_IMAGE="$OUT/recovery.img"
[[ -f "$SOURCE_IMAGE" ]] || {
    echo "REFUSING U0h: recovery builder produced no image" >&2
    exit 1
}
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$SOURCE_IMAGE" "$CANDIDATE"
SIZE="$(stat -Lc '%s' "$CANDIDATE")"
[[ "$SIZE" = 100663296 ]] || {
    echo "REFUSING U0h: unexpected recovery image size: $SIZE" >&2
    exit 1
}
if grep -Fq 'pmos_root=' "$OUT/final-boot-info.txt" 2>/dev/null; then
    echo "REFUSING U0h: kernel command line changed; U0h must isolate the node-creation delta" >&2
    exit 1
fi

SHA="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
{
    cat "$U0H_REPORT"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$SIZE"
    echo "recovery_sha256=$SHA"
    echo "build_status=passed"
} | tee "$MANIFEST"

echo
echo "U0h candidate prepared."
echo "Candidate: $CANDIDATE"
echo "SHA256:   $SHA"
echo "Manifest: $MANIFEST"

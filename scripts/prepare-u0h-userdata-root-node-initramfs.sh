#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
EXPORT_DIR="${EXPORT_DIR:-$PORT_ROOT/export-u0h-root-node}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
U0G_PREP="$SCRIPT_DIR/prepare-u0g-muic-dynamic-initramfs.sh"
U0G_REPORT="$PORT_ROOT/build/u0g-muic-dynamic.txt"
U0B_MODULES="$PORT_ROOT/build/u0b-embedded-modules.txt"
PACKAGE=postmarketos-mkinitfs-hook-a33x-userdata-root-node
PACKAGE_SOURCE="$REPO_ROOT/pmaports/main/$PACKAGE"
HOOK_SOURCE="$PACKAGE_SOURCE/05-a33x-userdata-root-node.sh"
REPORT="$PORT_ROOT/build/u0h-userdata-root-node.txt"

for command in pmbootstrap cp rm mkdir gzip cpio sha256sum python3 grep awk find readlink file; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in \
    "$ROOTFS" "$U0G_PREP" "$U0B_MODULES" \
    "$PACKAGE_SOURCE/APKBUILD" "$HOOK_SOURCE"; do
    [[ -e "$required" ]] || {
        echo "Missing required U0h input: $required" >&2
        exit 1
    }
done
bash -n "$HOOK_SOURCE"

report_value() {
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

echo "=== Recreate exact U0g functional base ==="
bash "$U0G_PREP"
[[ -f "$U0G_REPORT" ]] || {
    echo "REFUSING U0h: U0g preparation report is missing" >&2
    exit 1
}

PMAPORTS="${PMAPORTS:-$(pmbootstrap config aports)}"
PACKAGE_DEST="$PMAPORTS/main/$PACKAGE"

if pmbootstrap chroot -r -- apk info -e "$PACKAGE" >/dev/null 2>&1; then
    pmbootstrap chroot -r -- apk del "$PACKAGE"
fi
rm -rf "$PACKAGE_DEST"
mkdir -p "$(dirname "$PACKAGE_DEST")"
cp -a "$PACKAGE_SOURCE" "$PACKAGE_DEST"

pmbootstrap checksum "$PACKAGE"
pmbootstrap build --force "$PACKAGE"
pmbootstrap chroot -r --add "$PACKAGE" -- true

ROOTFS_HOOK="$ROOTFS/usr/share/mkinitfs/hooks/05-a33x-userdata-root-node.sh"
[[ -x "$ROOTFS_HOOK" ]] || {
    echo "REFUSING U0h: installed userdata-root hook is missing or non-executable" >&2
    exit 1
}
verify_sha root-node-installed "$ROOTFS_HOOK" "$(sha256sum "$HOOK_SOURCE" | awk '{print $1}')"

for required_text in \
    'root_name=sda36' \
    'expected_sectors=223125504' \
    'expected_label=pmOS_root' \
    'metadata_relative=a33x-bringup/u0h-root-node-result.txt' \
    'create_from_sysfs' \
    'mknod "$path" b "$major" "$minor"' \
    'blkid "$root_block_dev"'; do
    grep -Fq "$required_text" "$ROOTFS_HOOK" || {
        echo "REFUSING U0h: root-node hook contract text is missing: $required_text" >&2
        exit 1
    }
done
if grep -Eq 'dd .*of=/dev/(block/)?sda36|mkfs|wipefs' "$ROOTFS_HOOK"; then
    echo "REFUSING U0h: root-node hook contains a destructive userdata operation" >&2
    exit 1
fi

echo "=== Rebuild initramfs with one functional delta ==="
pmbootstrap chroot -r -- sh -ec 'mkinitfs'
ROOTFS_INITRAMFS="$ROOTFS/boot/initramfs"
[[ -f "$ROOTFS_INITRAMFS" ]] || {
    echo "REFUSING U0h: mkinitfs did not produce $ROOTFS_INITRAMFS" >&2
    exit 1
}
rm -rf "$EXPORT_DIR"
pmbootstrap export --no-install "$EXPORT_DIR"
INITRAMFS="$EXPORT_DIR/initramfs"
[[ -f "$INITRAMFS" ]] || {
    echo "REFUSING U0h: exported initramfs is missing" >&2
    exit 1
}

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
    init init_functions.sh init_2nd.sh init_functions_2nd.sh sysroot \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    hooks/05-a33x-userdata-root-node.sh \
    usr/libexec/a33x-muic-switch-dynamic; do
    [[ -e "$EXTRACT/$required" ]] || {
        echo "REFUSING U0h: initramfs is missing $required" >&2
        exit 1
    }
done
[[ -x "$EXTRACT/init_2nd.sh" && -x "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" ]] || {
    echo "REFUSING U0h: second stage or new hook is not executable" >&2
    exit 1
}

verify_sha typec-retained \
    "$(find "$EXTRACT" -type f -name 'usb_typec_manager.ko*' -print -quit)" \
    "$(report_value "$U0G_REPORT" retained_u0d_typec_sha256)"
verify_sha pdic-retained \
    "$(find "$EXTRACT" -type f -name 'pdic_notifier_module.ko*' -print -quit)" \
    "$(report_value "$U0G_REPORT" retained_u0d_pdic_sha256)"
verify_sha i2c-dev-retained \
    "$(find "$EXTRACT" -type f \( -name 'i2c-dev.ko*' -o -name 'i2c_dev.ko*' \) -print -quit)" \
    "$(report_value "$U0G_REPORT" retained_u0e_i2c_dev_sha256)"
verify_sha helper-retained "$EXTRACT/usr/libexec/a33x-muic-switch-dynamic" \
    "$(report_value "$U0G_REPORT" dynamic_helper_sha256)"
verify_sha hook03-retained "$EXTRACT/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$(report_value "$U0G_REPORT" dynamic_hook03_sha256)"
verify_sha hook04-retained "$EXTRACT/hooks/04-a33x-muic-persist-dynamic.sh" \
    "$(report_value "$U0G_REPORT" dynamic_hook04_sha256)"
verify_sha hook05-new "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" \
    "$(sha256sum "$HOOK_SOURCE" | awk '{print $1}')"

module_count="$(find "$EXTRACT/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0h: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

python3 - "$EXTRACT" > "$EXTRACT/runtime-tool-audit.txt" <<'PY'
from pathlib import Path
import os
import sys

root = Path(sys.argv[1])
tools = {
    "sh": ["/bin/sh"],
    "blkid": ["/sbin/blkid", "/usr/sbin/blkid", "/bin/blkid", "/usr/bin/blkid"],
    "mount": ["/bin/mount", "/sbin/mount", "/usr/bin/mount"],
    "switch_root": ["/sbin/switch_root", "/bin/switch_root", "/usr/sbin/switch_root"],
    "mknod": ["/bin/mknod", "/sbin/mknod", "/usr/bin/mknod"],
    "readlink": ["/bin/readlink", "/usr/bin/readlink"],
    "ln": ["/bin/ln", "/usr/bin/ln"],
    "mkdir": ["/bin/mkdir", "/usr/bin/mkdir"],
    "awk": ["/usr/bin/awk", "/bin/awk"],
    "grep": ["/bin/grep", "/usr/bin/grep"],
    "sed": ["/bin/sed", "/usr/bin/sed"],
    "sync": ["/bin/sync", "/usr/bin/sync"],
}

def present(path: str) -> tuple[bool, str]:
    full = root / path.lstrip("/")
    if not full.exists() and not full.is_symlink():
        return False, "missing"
    if full.is_symlink():
        link = os.readlink(full)
        if link.startswith("/"):
            target = root / link.lstrip("/")
        else:
            target = (full.parent / link).resolve(strict=False)
        return target.exists() or target.is_symlink(), f"symlink:{link}"
    return True, "file"

missing = []
for name, candidates in tools.items():
    matches = []
    for candidate in candidates:
        ok, kind = present(candidate)
        if ok:
            matches.append(f"{candidate}:{kind}")
    if not matches:
        missing.append(name)
        print(f"runtime_tool={name} status=missing")
    else:
        print(f"runtime_tool={name} status=present paths={','.join(matches)}")
if missing:
    raise SystemExit("missing initramfs runtime tools: " + ", ".join(missing))
PY
cat "$EXTRACT/runtime-tool-audit.txt"

for token in find_root_partition pmOS_root wait_root_partition mount_root_partition switch_root; do
    grep -Fq "$token" "$EXTRACT/init_functions.sh" "$EXTRACT/init_2nd.sh" "$EXTRACT/init_functions_2nd.sh" || {
        echo "REFUSING U0h: root handoff token is missing: $token" >&2
        exit 1
    }
done

INITRAMFS_SHA="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
HOOK_SHA="$(sha256sum "$HOOK_SOURCE" | awk '{print $1}')"
{
    echo "candidate=U0h-userdata-root-node"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_base=U0g-muic-dynamic"
    echo "functional_delta=create-verified-sda36-device-nodes-before-root-discovery"
    echo "kernel_cmdline_delta=none"
    echo "module_delta=none"
    echo "expected_userdata=/dev/block/sda36"
    echo "expected_userdata_sectors=223125504"
    echo "expected_root_label=pmOS_root"
    echo "root_node_hook=$HOOK_SOURCE"
    echo "root_node_hook_sha256=$HOOK_SHA"
    echo "embedded_modules=$module_count"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_sha256=$INITRAMFS_SHA"
    echo "root_handoff_runtime_tools=passed"
    echo "preparation_status=passed"
    echo "phone_partition_writes=no"
} | tee "$REPORT"

cleanup
trap - EXIT

echo
echo "U0h userdata-root-node initramfs prepared."
echo "Initramfs: $INITRAMFS"
echo "Report:    $REPORT"
echo "No phone partition was written."

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

ROOT="${ROOT:-$HOME/a33-port}"
ROOTFS="${ROOTFS:-$HOME/.local/var/pmbootstrap/chroot_rootfs_samsung-a33x}"
INITRAMFS="${INITRAMFS:-$ROOT/export-u0h-root-node/initramfs}"
U0G_REPORT="${U0G_REPORT:-$ROOT/build/u0g-muic-dynamic.txt}"
REPORT="${REPORT:-$ROOT/build/u0h-userdata-root-node.txt}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
HOOK_SOURCE="$REPO_ROOT/pmaports/main/postmarketos-mkinitfs-hook-a33x-userdata-root-node/05-a33x-userdata-root-node.sh"

for command in \
    pmbootstrap gzip cpio sha256sum cmp python3 grep awk find stat mktemp rm \
    date tee sort file; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in "$ROOTFS" "$INITRAMFS" "$U0G_REPORT" "$HOOK_SOURCE"; do
    [[ -e "$required" ]] || {
        echo "REFUSING U0h: required finalized-preparation input is missing: $required" >&2
        exit 1
    }
done

value() {
    local file="$1" key="$2"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$file"
}
verify_sha() {
    local label="$1" path="$2" expected="$3" actual
    [[ -f "$path" ]] || {
        echo "REFUSING U0h: $label file is missing: $path" >&2
        exit 1
    }
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING U0h: $label SHA256 mismatch" >&2
        echo "expected=$expected actual=$actual path=$path" >&2
        exit 1
    fi
}

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
EXTRACT="$TMP/initramfs"
mkdir -p "$EXTRACT"
gzip -dc "$INITRAMFS" > "$TMP/initramfs.cpio"
(
    cd "$EXTRACT"
    cpio -idmu --quiet < "$TMP/initramfs.cpio"
)

for required in \
    init init_functions.sh init_2nd.sh init_functions_2nd.sh sysroot \
    bin/busybox bin/busybox-extras \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    hooks/05-a33x-userdata-root-node.sh \
    usr/libexec/a33x-muic-switch-dynamic; do
    [[ -e "$EXTRACT/$required" || -L "$EXTRACT/$required" ]] || {
        echo "REFUSING U0h: exported initramfs is missing $required" >&2
        exit 1
    }
done
[[ -x "$EXTRACT/init_2nd.sh" && -x "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" ]] || {
    echo "REFUSING U0h: second stage or hook 05 is not executable" >&2
    exit 1
}

verify_sha typec-retained \
    "$(find "$EXTRACT" -type f -name 'usb_typec_manager.ko*' -print -quit)" \
    "$(value "$U0G_REPORT" retained_u0d_typec_sha256)"
verify_sha pdic-retained \
    "$(find "$EXTRACT" -type f -name 'pdic_notifier_module.ko*' -print -quit)" \
    "$(value "$U0G_REPORT" retained_u0d_pdic_sha256)"
verify_sha i2c-dev-retained \
    "$(find "$EXTRACT" -type f \( -name 'i2c-dev.ko*' -o -name 'i2c_dev.ko*' \) -print -quit)" \
    "$(value "$U0G_REPORT" retained_u0e_i2c_dev_sha256)"
verify_sha helper-retained "$EXTRACT/usr/libexec/a33x-muic-switch-dynamic" \
    "$(value "$U0G_REPORT" dynamic_helper_sha256)"
verify_sha hook03-retained "$EXTRACT/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook03_sha256)"
verify_sha hook04-retained "$EXTRACT/hooks/04-a33x-muic-persist-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook04_sha256)"
HOOK_SHA="$(sha256sum "$HOOK_SOURCE" | awk '{print $1}')"
verify_sha hook05-new "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" "$HOOK_SHA"

module_count="$(find "$EXTRACT/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0h: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

for token in \
    'root_name=sda36' \
    'expected_sectors=223125504' \
    'expected_label=pmOS_root' \
    'metadata_relative=a33x-bringup/u0h-root-node-result.txt' \
    'create_from_sysfs' \
    'mknod "$path" b "$major" "$minor"' \
    'blkid "$root_block_dev"'; do
    grep -Fq "$token" "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" || {
        echo "REFUSING U0h: embedded hook 05 contract is missing: $token" >&2
        exit 1
    }
done
if grep -Eq 'dd .*of=/dev/(block/)?sda36|mkfs|wipefs|parted|sgdisk' \
    "$EXTRACT/hooks/05-a33x-userdata-root-node.sh"; then
    echo "REFUSING U0h: embedded hook 05 contains a destructive operation" >&2
    exit 1
fi

# postmarketOS intentionally stores BusyBox multicall binaries in the archive
# and creates the applet symlinks at runtime with --install -s before hooks run.
# Bind the applet lists queried in the rootfs to the exact embedded binaries.
for binary in busybox busybox-extras; do
    [[ -x "$ROOTFS/bin/$binary" ]] || {
        echo "REFUSING U0h: rootfs lacks executable /bin/$binary" >&2
        exit 1
    }
    cmp "$EXTRACT/bin/$binary" "$ROOTFS/bin/$binary" || {
        echo "REFUSING U0h: embedded /bin/$binary differs from rootfs binary" >&2
        exit 1
    }
done

BUSYBOX_LIST="$TMP/busybox-applets.txt"
BUSYBOX_EXTRAS_LIST="$TMP/busybox-extras-applets.txt"
pmbootstrap chroot -r -- /bin/busybox --list > "$BUSYBOX_LIST"
pmbootstrap chroot -r -- /bin/busybox-extras --list > "$BUSYBOX_EXTRAS_LIST"
cat "$BUSYBOX_LIST" "$BUSYBOX_EXTRAS_LIST" | sort -u > "$TMP/all-applets.txt"

explicit_command_present() {
    local name="$1" candidate
    for candidate in \
        "$EXTRACT/bin/$name" "$EXTRACT/sbin/$name" \
        "$EXTRACT/usr/bin/$name" "$EXTRACT/usr/sbin/$name"; do
        if [[ -e "$candidate" || -L "$candidate" ]]; then
            return 0
        fi
    done
    return 1
}

RUNTIME_TOOLS=(
    sh mount umount mknod readlink ln mkdir awk grep sed sync cat chmod mv rm
    sleep wc tail tr blkid switch_root resize2fs e2fsck
)
: > "$TMP/runtime-tool-audit.txt"
for tool in "${RUNTIME_TOOLS[@]}"; do
    if grep -Fxq "$tool" "$TMP/all-applets.txt"; then
        echo "runtime_tool=$tool provider=busybox-applet status=present" \
            | tee -a "$TMP/runtime-tool-audit.txt"
    elif explicit_command_present "$tool"; then
        echo "runtime_tool=$tool provider=embedded-file status=present" \
            | tee -a "$TMP/runtime-tool-audit.txt"
    else
        echo "REFUSING U0h: runtime tool is unavailable after BusyBox installation: $tool" >&2
        cat "$TMP/runtime-tool-audit.txt" >&2
        exit 1
    fi
done

ORDER_RESULT="$(
    python3 - "$EXTRACT/init" "$EXTRACT/init_2nd.sh" <<'PY'
from pathlib import Path
import re
import sys

init_lines = Path(sys.argv[1]).read_text(errors="replace").splitlines()
init2_lines = Path(sys.argv[2]).read_text(errors="replace").splitlines()

def first(lines, pattern):
    for number, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pattern.search(stripped):
            return number, stripped
    return None

busybox = first(init_lines, re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*/bin/busybox\s+--install\s+-s(?:$|[;&|])"))
extras = first(init_lines, re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*/bin/busybox-extras\s+--install\s+-s(?:$|[;&|])"))
jump = first(init_lines, re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*jump_init_2nd(?:$|[\s;&|])"))
hooks = first(init2_lines, re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*run_hooks\s+[\"']?/hooks[\"']?(?:$|[\s;&|])"))
wait = first(init2_lines, re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*wait_root_partition(?:$|[\s;&|])"))

for name, item in (("busybox install", busybox), ("busybox-extras install", extras), ("jump_init_2nd", jump), ("run_hooks", hooks), ("wait_root_partition", wait)):
    if item is None:
        raise SystemExit(f"missing executable {name} call")
if not (busybox[0] < jump[0] and extras[0] < jump[0]):
    raise SystemExit(f"BusyBox applets are not installed before second stage: busybox={busybox[0]} extras={extras[0]} jump={jump[0]}")
if hooks[0] >= wait[0]:
    raise SystemExit(f"hooks do not run before root discovery: hooks={hooks[0]} wait={wait[0]}")

print(f"busybox_install_line={busybox[0]}")
print(f"busybox_extras_install_line={extras[0]}")
print(f"jump_init_2nd_line={jump[0]}")
print(f"run_hooks_line={hooks[0]}")
print(f"wait_root_partition_line={wait[0]}")
print("busybox_install_before_second_stage=yes")
print("hook_before_root_discovery=yes")
PY
)"
printf '%s\n' "$ORDER_RESULT"

for token in find_root_partition pmOS_root wait_root_partition mount_root_partition switch_root; do
    grep -Fq "$token" \
        "$EXTRACT/init_functions.sh" "$EXTRACT/init_2nd.sh" "$EXTRACT/init_functions_2nd.sh" || {
        echo "REFUSING U0h: root-handoff token is missing: $token" >&2
        exit 1
    }
done

INITRAMFS_SHA="$(sha256sum "$INITRAMFS" | awk '{print $1}')"
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
    echo "busybox_binary_sha256=$(sha256sum "$EXTRACT/bin/busybox" | awk '{print $1}')"
    echo "busybox_extras_binary_sha256=$(sha256sum "$EXTRACT/bin/busybox-extras" | awk '{print $1}')"
    echo "busybox_runtime_applets=passed"
    echo "root_handoff_runtime_tools=passed"
    printf '%s\n' "$ORDER_RESULT"
    echo "hook_order_validation=passed"
    echo "preparation_status=passed"
    echo "phone_partition_writes=no"
} | tee "$REPORT"

cleanup
trap - EXIT

echo
echo "U0h existing initramfs finalized and fully audited."
echo "Initramfs: $INITRAMFS"
echo "Report:    $REPORT"
echo "No phone partition was written."

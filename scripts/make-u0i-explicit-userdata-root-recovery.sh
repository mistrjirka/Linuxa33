#!/usr/bin/env bash
set -Eeuo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

ROOT="${ROOT:-$HOME/a33-port}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INITRAMFS="${INITRAMFS:-$ROOT/export-u0h-root-node/initramfs}"
U0H_REPORT="${U0H_REPORT:-$ROOT/build/u0h-userdata-root-node.txt}"
U0G_REPORT="${U0G_REPORT:-$ROOT/build/u0g-muic-dynamic.txt}"
OUT="${OUT:-$ROOT/build/pmos-debug-recovery-u0i}"
CANDIDATE="${CANDIDATE:-$ROOT/build/candidates/a33x-h1-usbpd-u0i-explicit-userdata-root-recovery.img}"
MANIFEST="${MANIFEST:-$ROOT/build/candidates/a33x-h1-usbpd-u0i-explicit-userdata-root-manifest.txt}"
EXPLICIT_ROOT="/dev/block/sda36"
CMDLINE_TOKEN="pmos_root=$EXPLICIT_ROOT"

for command in \
    gzip cpio sha256sum python3 stat cp grep awk find file mktemp rm mkdir ln; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in \
    "$INITRAMFS" "$U0H_REPORT" "$U0G_REPORT" \
    "$SCRIPT_DIR/make-pmos-debug-recovery.sh"; do
    [[ -e "$required" ]] || {
        echo "Missing required U0i input: $required" >&2
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
        echo "REFUSING U0i: $label file is missing: $path" >&2
        exit 1
    }
    actual="$(sha256sum "$path" | awk '{print $1}')"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$actual" != "$expected" ]]; then
        echo "REFUSING U0i: $label SHA256 mismatch" >&2
        echo "expected=$expected actual=$actual path=$path" >&2
        exit 1
    fi
}

if [[ "$(value "$U0H_REPORT" preparation_status)" != passed || \
      "$(value "$U0H_REPORT" busybox_runtime_applets)" != passed || \
      "$(value "$U0H_REPORT" root_handoff_runtime_tools)" != passed || \
      "$(value "$U0H_REPORT" busybox_install_before_second_stage)" != yes || \
      "$(value "$U0H_REPORT" hook_before_root_discovery)" != yes || \
      "$(value "$U0H_REPORT" hook_order_validation)" != passed || \
      "$(value "$U0H_REPORT" phone_partition_writes)" != no ]]; then
    echo "REFUSING U0i: U0h preparation report did not pass the exact contract" >&2
    cat "$U0H_REPORT" >&2
    exit 1
fi
verify_sha initramfs "$INITRAMFS" "$(value "$U0H_REPORT" initramfs_sha256)"

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
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    hooks/05-a33x-userdata-root-node.sh \
    usr/libexec/a33x-muic-switch-dynamic; do
    [[ -e "$EXTRACT/$required" || -L "$EXTRACT/$required" ]] || {
        echo "REFUSING U0i: U0h initramfs is missing $required" >&2
        exit 1
    }
done

verify_sha helper-retained "$EXTRACT/usr/libexec/a33x-muic-switch-dynamic" \
    "$(value "$U0G_REPORT" dynamic_helper_sha256)"
verify_sha hook03-retained "$EXTRACT/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook03_sha256)"
verify_sha hook04-retained "$EXTRACT/hooks/04-a33x-muic-persist-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook04_sha256)"
verify_sha hook05-retained "$EXTRACT/hooks/05-a33x-userdata-root-node.sh" \
    "$(value "$U0H_REPORT" root_node_hook_sha256)"

module_count="$(find "$EXTRACT/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0i: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

ROOT_CONTRACT="$(
    python3 - "$EXTRACT/init_functions.sh" "$EXTRACT/init_2nd.sh" "$EXPLICIT_ROOT" <<'PY'
from pathlib import Path
import re
import sys

functions_path = Path(sys.argv[1])
init2_path = Path(sys.argv[2])
explicit_root = sys.argv[3]
functions = functions_path.read_text(errors="replace")
init2 = init2_path.read_text(errors="replace")


def body(name: str, text: str) -> str:
    match = re.search(
        rf"(?ms)^\s*{re.escape(name)}\s*\(\s*\)\s*\{{\s*\n(?P<body>.*?)^\s*\}}",
        text,
    )
    if match is None:
        raise SystemExit(f"missing function: {name}")
    return match.group("body")

find_body = body("find_root_partition", functions)
wait_body = body("wait_root_partition", functions)

required_find = {
    "cmdline_read": "/proc/cmdline",
    "pmos_root_token": "pmos_root=",
    "prefix_removal": "${x#pmos_root=}",
    "device_assignment": "DEVICE=",
}
for name, token in required_find.items():
    if token not in find_body:
        raise SystemExit(f"find_root_partition lacks {name}: {token}")
if "find_root_partition" not in wait_body:
    raise SystemExit("wait_root_partition does not call find_root_partition")
if "run_hooks /hooks" not in init2:
    raise SystemExit("init_2nd.sh does not run normal hooks")
if "wait_root_partition" not in init2:
    raise SystemExit("init_2nd.sh does not wait for the root partition")

print("pmos_root_cmdline_parser=passed")
print(f"explicit_root={explicit_root}")
print("find_root_partition_contract=passed")
print("wait_root_partition_contract=passed")
PY
)"
printf '%s\n' "$ROOT_CONTRACT"

cleanup
trap - EXIT
rm -rf "$OUT"

# Reuse the exact U0h initramfs. U0i changes only the recovery kernel command
# line so the already-created, verified userdata node is selected explicitly.
BUILD_ROOT="$(mktemp -d)"
cleanup_build_root() { rm -rf "$BUILD_ROOT"; }
trap cleanup_build_root EXIT
mkdir -p "$BUILD_ROOT/export-debug"
ln -s "$ROOT/reference" "$BUILD_ROOT/reference"
ln -s "$ROOT/aosp-mkbootimg" "$BUILD_ROOT/aosp-mkbootimg"
ln -s "$ROOT/aosp-avb" "$BUILD_ROOT/aosp-avb"
ln -s "$ROOT/build" "$BUILD_ROOT/build"
cp --reflink=auto "$INITRAMFS" "$BUILD_ROOT/export-debug/initramfs"

env \
    ROOT="$BUILD_ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="$CMDLINE_TOKEN" \
    bash "$SCRIPT_DIR/make-pmos-debug-recovery.sh"

cleanup_build_root
trap - EXIT

SOURCE_IMAGE="$OUT/recovery.img"
[[ -f "$SOURCE_IMAGE" ]] || {
    echo "REFUSING U0i: recovery builder produced no image" >&2
    exit 1
}

python3 - "$OUT/final-boot-info.txt" "$CMDLINE_TOKEN" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
token = sys.argv[2]
text = path.read_text(errors="replace")
found = re.findall(r"(?<!\S)pmos_root=\S+", text)
if found != [token]:
    raise SystemExit(f"expected exactly one explicit root token {token!r}, found {found!r}")
print("final_cmdline_explicit_root=passed")
PY

mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$SOURCE_IMAGE" "$CANDIDATE"
SIZE="$(stat -Lc '%s' "$CANDIDATE")"
if [[ "$SIZE" != 100663296 ]]; then
    echo "REFUSING U0i: unexpected recovery image size: $SIZE" >&2
    exit 1
fi
SHA="$(sha256sum "$CANDIDATE" | awk '{print $1}')"

{
    echo "candidate=U0i-explicit-userdata-root"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_base=U0h-userdata-root-node"
    echo "functional_delta=explicit-pmos-root-device"
    echo "root_autodetection_result_u0h=failed-after-verified-node"
    echo "kernel_cmdline_delta=$CMDLINE_TOKEN"
    echo "module_delta=none"
    echo "initramfs_delta=none-from-u0h"
    echo "explicit_root=$EXPLICIT_ROOT"
    echo "u0h_report=$U0H_REPORT"
    echo "u0h_report_sha256=$(sha256sum "$U0H_REPORT" | awk '{print $1}')"
    echo "u0g_report=$U0G_REPORT"
    echo "u0g_report_sha256=$(sha256sum "$U0G_REPORT" | awk '{print $1}')"
    echo "initramfs=$INITRAMFS"
    echo "initramfs_sha256=$(sha256sum "$INITRAMFS" | awk '{print $1}')"
    echo "root_node_hook_sha256=$(value "$U0H_REPORT" root_node_hook_sha256)"
    echo "embedded_modules=$module_count"
    echo "busybox_runtime_applets=passed"
    echo "root_handoff_runtime_tools=passed"
    echo "busybox_install_before_second_stage=yes"
    echo "hook_before_root_discovery=yes"
    echo "hook_order_validation=passed"
    printf '%s\n' "$ROOT_CONTRACT"
    echo "preparation_status=passed"
    echo "phone_partition_writes=no"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$SIZE"
    echo "recovery_sha256=$SHA"
    echo "build_status=passed"
} | tee "$MANIFEST"

echo
echo "U0i explicit-root candidate prepared."
echo "Candidate: $CANDIDATE"
echo "SHA256:   $SHA"
echo "Manifest: $MANIFEST"
echo "No phone partition was written."

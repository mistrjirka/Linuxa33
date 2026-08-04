#!/usr/bin/env bash
set -Eeuo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

ROOT="${ROOT:-$HOME/a33-port}"
EXPECTED_MODULE_COUNT="${EXPECTED_MODULE_COUNT:-67}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
U0H_INITRAMFS="${U0H_INITRAMFS:-$ROOT/export-u0h-root-node/initramfs}"
U0H_REPORT="${U0H_REPORT:-$ROOT/build/u0h-userdata-root-node.txt}"
U0G_REPORT="${U0G_REPORT:-$ROOT/build/u0g-muic-dynamic.txt}"
HOOK06_SOURCE="$REPO_ROOT/pmaports/main/postmarketos-mkinitfs-hook-a33x-forced-root-function/06-a33x-forced-root-function.sh"
OUT_INITRAMFS_DIR="${OUT_INITRAMFS_DIR:-$ROOT/export-u0i-forced-root-function}"
U0I_INITRAMFS="$OUT_INITRAMFS_DIR/initramfs"
OUT="${OUT:-$ROOT/build/pmos-debug-recovery-u0i-forced-root-function}"
CANDIDATE="${CANDIDATE:-$ROOT/build/candidates/a33x-h1-usbpd-u0i-forced-root-function-recovery.img}"
MANIFEST="${MANIFEST:-$ROOT/build/candidates/a33x-h1-usbpd-u0i-forced-root-function-manifest.txt}"
HOOK06_ENTRY=hooks/06-a33x-forced-root-function.sh

for command in \
    gzip cpio sha256sum python3 stat cp grep awk find file mktemp rm mkdir \
    ln sort cmp chmod date tee; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in \
    "$U0H_INITRAMFS" "$U0H_REPORT" "$U0G_REPORT" "$HOOK06_SOURCE" \
    "$SCRIPT_DIR/make-pmos-debug-recovery.sh"; do
    [[ -e "$required" ]] || {
        echo "Missing required U0i input: $required" >&2
        exit 1
    }
done
bash -n "$HOOK06_SOURCE"
cpio --help 2>&1 | grep -q -- '--owner' || {
    echo "REFUSING U0i: host cpio lacks --owner support" >&2
    exit 1
}

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
    echo "REFUSING U0i: U0h preparation report did not pass" >&2
    cat "$U0H_REPORT" >&2
    exit 1
fi
verify_sha u0h-initramfs "$U0H_INITRAMFS" "$(value "$U0H_REPORT" initramfs_sha256)"

TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
BASE="$TMP/base"
VERIFY="$TMP/verify"
mkdir -p "$BASE" "$VERIFY"
gzip -dc "$U0H_INITRAMFS" > "$TMP/u0h.cpio"
(
    cd "$BASE"
    cpio -idmu --quiet < "$TMP/u0h.cpio"
)

for required in \
    init init_functions.sh init_2nd.sh init_functions_2nd.sh sysroot \
    hooks/01-a33x-watchdog.sh \
    hooks/02-a33x-usbpd-load.sh \
    hooks/03-a33x-muic-switch-dynamic.sh \
    hooks/04-a33x-muic-persist-dynamic.sh \
    hooks/05-a33x-userdata-root-node.sh \
    usr/libexec/a33x-muic-switch-dynamic; do
    [[ -e "$BASE/$required" || -L "$BASE/$required" ]] || {
        echo "REFUSING U0i: U0h initramfs is missing $required" >&2
        exit 1
    }
done
[[ ! -e "$BASE/$HOOK06_ENTRY" && ! -L "$BASE/$HOOK06_ENTRY" ]] || {
    echo "REFUSING U0i: forced-root hook already exists in U0h base" >&2
    exit 1
}

verify_sha helper-retained "$BASE/usr/libexec/a33x-muic-switch-dynamic" \
    "$(value "$U0G_REPORT" dynamic_helper_sha256)"
verify_sha hook03-retained "$BASE/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook03_sha256)"
verify_sha hook04-retained "$BASE/hooks/04-a33x-muic-persist-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook04_sha256)"
verify_sha hook05-retained "$BASE/hooks/05-a33x-userdata-root-node.sh" \
    "$(value "$U0H_REPORT" root_node_hook_sha256)"

module_count="$(find "$BASE/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
if [[ "$module_count" != "$EXPECTED_MODULE_COUNT" ]]; then
    echo "REFUSING U0i: expected $EXPECTED_MODULE_COUNT modules, found $module_count" >&2
    exit 1
fi

SOURCING_CONTRACT="$(
    python3 - "$BASE/init_functions.sh" "$BASE/init_2nd.sh" <<'PY'
from pathlib import Path
import re
import sys

functions = Path(sys.argv[1]).read_text(errors="replace")
init2_lines = Path(sys.argv[2]).read_text(errors="replace").splitlines()

match = re.search(
    r"(?ms)^\s*run_hooks\s*\(\s*\)\s*\{\s*\n(?P<body>.*?)^\s*\}",
    functions,
)
if match is None:
    raise SystemExit("run_hooks() not found")
body = match.group("body")
if re.search(r"(?m)^\s*(?:\.|source)\s+[\"']?\$[A-Za-z_][A-Za-z0-9_]*[\"']?", body) is None:
    raise SystemExit("run_hooks does not source hook files into the current shell")
if "sort" not in body:
    raise SystemExit("run_hooks does not prove deterministic hook ordering")

def first(pattern):
    for number, raw in enumerate(init2_lines, 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if pattern.search(line):
            return number, line
    return None

hooks = first(re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*run_hooks\s+[\"']?/hooks[\"']?(?:$|[\s;&|])"))
wait = first(re.compile(r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*wait_root_partition(?:$|[\s;&|])"))
if hooks is None or wait is None:
    raise SystemExit(f"missing hook/wait calls: hooks={hooks} wait={wait}")
if hooks[0] >= wait[0]:
    raise SystemExit(f"hooks execute after root discovery: hooks={hooks[0]} wait={wait[0]}")

print("run_hooks_sources_current_shell=yes")
print("run_hooks_sorted_order=yes")
print(f"run_hooks_line={hooks[0]}")
print(f"wait_root_partition_line={wait[0]}")
print("hook_before_root_discovery=yes")
PY
)"
printf '%s\n' "$SOURCING_CONTRACT"

python3 - "$BASE" "$TMP/base-manifest.txt" <<'PY'
from pathlib import Path
import hashlib
import os
import stat
import sys

root = Path(sys.argv[1])
out = Path(sys.argv[2])
rows = []
for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
    rel = path.relative_to(root).as_posix()
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    if path.is_symlink():
        rows.append(f"L {mode:04o} {rel} -> {os.readlink(path)}")
    elif path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"F {mode:04o} {digest} {rel}")
    elif path.is_dir():
        rows.append(f"D {mode:04o} {rel}")
    else:
        rows.append(f"O {mode:04o} {rel}")
out.write_text("\n".join(rows) + "\n")
PY

install -Dm755 "$HOOK06_SOURCE" "$BASE/$HOOK06_ENTRY"
HOOK06_SHA="$(sha256sum "$HOOK06_SOURCE" | awk '{print $1}')"
verify_sha hook06-injected "$BASE/$HOOK06_ENTRY" "$HOOK06_SHA"

mkdir -p "$OUT_INITRAMFS_DIR"
rm -f "$U0I_INITRAMFS"
(
    cd "$BASE"
    find . -mindepth 1 -print0 \
        | sort -z \
        | cpio --null -o -H newc --owner=0:0 --quiet \
        | gzip -n > "$U0I_INITRAMFS"
)
[[ -s "$U0I_INITRAMFS" ]] || {
    echo "REFUSING U0i: repacked initramfs is empty" >&2
    exit 1
}

gzip -dc "$U0I_INITRAMFS" > "$TMP/u0i.cpio"
(
    cd "$VERIFY"
    cpio -idmu --quiet < "$TMP/u0i.cpio"
)
verify_sha hook06-output "$VERIFY/$HOOK06_ENTRY" "$HOOK06_SHA"

python3 - "$VERIFY" "$HOOK06_ENTRY" "$TMP/verify-base-manifest.txt" <<'PY'
from pathlib import Path
import hashlib
import os
import stat
import sys

root = Path(sys.argv[1])
exclude = sys.argv[2]
out = Path(sys.argv[3])
rows = []
for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
    rel = path.relative_to(root).as_posix()
    if rel == exclude:
        continue
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    if path.is_symlink():
        rows.append(f"L {mode:04o} {rel} -> {os.readlink(path)}")
    elif path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"F {mode:04o} {digest} {rel}")
    elif path.is_dir():
        rows.append(f"D {mode:04o} {rel}")
    else:
        rows.append(f"O {mode:04o} {rel}")
out.write_text("\n".join(rows) + "\n")
PY
if ! cmp -s "$TMP/base-manifest.txt" "$TMP/verify-base-manifest.txt"; then
    echo "REFUSING U0i: repacking changed U0h content beyond hook 06" >&2
    diff -u "$TMP/base-manifest.txt" "$TMP/verify-base-manifest.txt" >&2 || true
    exit 1
fi

u0i_module_count="$(find "$VERIFY/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
[[ "$u0i_module_count" = "$EXPECTED_MODULE_COUNT" ]] || {
    echo "REFUSING U0i: repacked module count changed: $u0i_module_count" >&2
    exit 1
}

rm -rf "$OUT"
BUILD_ROOT="$(mktemp -d)"
cleanup_build_root() { rm -rf "$BUILD_ROOT"; }
trap 'cleanup_build_root; cleanup' EXIT
mkdir -p "$BUILD_ROOT/export-debug"
ln -s "$ROOT/reference" "$BUILD_ROOT/reference"
ln -s "$ROOT/aosp-mkbootimg" "$BUILD_ROOT/aosp-mkbootimg"
ln -s "$ROOT/aosp-avb" "$BUILD_ROOT/aosp-avb"
ln -s "$ROOT/build" "$BUILD_ROOT/build"
cp --reflink=auto "$U0I_INITRAMFS" "$BUILD_ROOT/export-debug/initramfs"

env \
    ROOT="$BUILD_ROOT" \
    OUT="$OUT" \
    EXTRA_KERNEL_CMDLINE="" \
    bash "$SCRIPT_DIR/make-pmos-debug-recovery.sh"

cleanup_build_root
trap cleanup EXIT
SOURCE_IMAGE="$OUT/recovery.img"
[[ -f "$SOURCE_IMAGE" ]] || {
    echo "REFUSING U0i: recovery builder produced no image" >&2
    exit 1
}
if grep -Eq '(^|[[:space:]])pmos_root=' "$OUT/final-boot-info.txt" 2>/dev/null; then
    echo "REFUSING U0i: kernel command line changed unexpectedly" >&2
    exit 1
fi
mkdir -p "$(dirname "$CANDIDATE")"
cp --reflink=auto "$SOURCE_IMAGE" "$CANDIDATE"
SIZE="$(stat -Lc '%s' "$CANDIDATE")"
[[ "$SIZE" = 100663296 ]] || {
    echo "REFUSING U0i: unexpected recovery size: $SIZE" >&2
    exit 1
}
SHA="$(sha256sum "$CANDIDATE" | awk '{print $1}')"
U0I_INITRAMFS_SHA="$(sha256sum "$U0I_INITRAMFS" | awk '{print $1}')"

{
    echo "candidate=U0i-forced-root-function"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$(git -C "$REPO_ROOT" rev-parse HEAD)"
    echo "functional_base=U0h-userdata-root-node"
    echo "functional_delta=source-hook06-and-override-find_root_partition"
    echo "kernel_cmdline_delta=none"
    echo "module_delta=none"
    echo "u0h_initramfs=$U0H_INITRAMFS"
    echo "u0h_initramfs_sha256=$(sha256sum "$U0H_INITRAMFS" | awk '{print $1}')"
    echo "u0i_initramfs=$U0I_INITRAMFS"
    echo "u0i_initramfs_sha256=$U0I_INITRAMFS_SHA"
    echo "root_function_hook=$HOOK06_SOURCE"
    echo "root_function_hook_sha256=$HOOK06_SHA"
    echo "forced_root=/dev/block/sda36"
    echo "embedded_modules=$u0i_module_count"
    echo "base_tree_unchanged_except_hook06=yes"
    printf '%s\n' "$SOURCING_CONTRACT"
    echo "hook_order_validation=passed"
    echo "preparation_status=passed"
    echo "phone_partition_writes=no"
    echo "recovery=$CANDIDATE"
    echo "recovery_size=$SIZE"
    echo "recovery_sha256=$SHA"
    echo "build_status=passed"
} | tee "$MANIFEST"

cleanup
trap - EXIT

echo
echo "U0i forced-root-function candidate prepared."
echo "Candidate: $CANDIDATE"
echo "SHA256:   $SHA"
echo "Manifest: $MANIFEST"
echo "No phone partition was written."

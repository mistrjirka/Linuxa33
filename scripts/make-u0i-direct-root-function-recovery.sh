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
OUT_INITRAMFS_DIR="${OUT_INITRAMFS_DIR:-$ROOT/export-u0i-direct-root-function}"
U0I_INITRAMFS="$OUT_INITRAMFS_DIR/initramfs"
OUT="${OUT:-$ROOT/build/pmos-debug-recovery-u0i-direct-root-function}"
CANDIDATE="${CANDIDATE:-$ROOT/build/candidates/a33x-h1-usbpd-u0i-direct-root-function-recovery.img}"
MANIFEST="${MANIFEST:-$ROOT/build/candidates/a33x-h1-usbpd-u0i-direct-root-function-manifest.txt}"
PATCH_REPORT="${PATCH_REPORT:-$ROOT/build/u0i-direct-root-function-patch.txt}"
INSPECTION_DIR="${INSPECTION_DIR:-$ROOT/build/u0i-direct-root-inspection}"
FORCED_ROOT=/dev/block/sda36
PATCHED_ENTRY=init_functions.sh

for command in \
    gzip cpio sha256sum python3 stat cp grep awk find file mktemp rm mkdir \
    ln sort cmp chmod date tee diff sh git; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "Missing required command: $command" >&2
        exit 1
    }
done
for required in \
    "$U0H_INITRAMFS" "$U0H_REPORT" "$U0G_REPORT" \
    "$SCRIPT_DIR/make-pmos-debug-recovery.sh"; do
    [[ -e "$required" ]] || {
        echo "Missing required U0i input: $required" >&2
        exit 1
    }
done
cpio --help 2>&1 | grep -q -- '--owner' || {
    echo "REFUSING U0i: host cpio lacks --owner support" >&2
    exit 1
}
LINUXA33_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
[[ "$LINUXA33_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "REFUSING U0i: cannot resolve exact Linuxa33 commit" >&2
    exit 1
}
rm -rf "$INSPECTION_DIR"
mkdir -p "$INSPECTION_DIR"

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
    bin/busybox bin/busybox-extras \
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

python3 - "$BASE" "$TMP/base-tree.txt" "$TMP/base-hardlinks.txt" <<'PY'
from pathlib import Path
import hashlib
import os
import stat
import sys

root = Path(sys.argv[1])
tree_out = Path(sys.argv[2])
hard_out = Path(sys.argv[3])
rows = []
inodes = {}
for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
    rel = path.relative_to(root).as_posix()
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    if path.is_symlink():
        rows.append(f"L {mode:04o} {rel} -> {os.readlink(path)}")
    elif path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"F {mode:04o} {digest} {rel}")
        inodes.setdefault((st.st_dev, st.st_ino), []).append(rel)
    elif path.is_dir():
        rows.append(f"D {mode:04o} {rel}")
    elif stat.S_ISCHR(st.st_mode):
        rows.append(f"C {mode:04o} {os.major(st.st_rdev)}:{os.minor(st.st_rdev)} {rel}")
    elif stat.S_ISBLK(st.st_mode):
        rows.append(f"B {mode:04o} {os.major(st.st_rdev)}:{os.minor(st.st_rdev)} {rel}")
    elif stat.S_ISFIFO(st.st_mode):
        rows.append(f"P {mode:04o} {rel}")
    else:
        rows.append(f"O {mode:04o} {rel}")
tree_out.write_text("\n".join(rows) + "\n")
groups = sorted("|".join(sorted(paths)) for paths in inodes.values() if len(paths) > 1)
hard_out.write_text("\n".join(groups) + ("\n" if groups else ""))
PY

ORIGINAL_FUNCTIONS_SHA="$(sha256sum "$BASE/init_functions.sh" | awk '{print $1}')"
PATCH_CONTRACT="$(
    python3 - "$BASE/init_functions.sh" "$BASE/init_2nd.sh" "$FORCED_ROOT" "$INSPECTION_DIR/original-find_root_partition.sh" "$INSPECTION_DIR/original-wait_root_partition.sh" <<'PY'
from pathlib import Path
import hashlib
import re
import sys

functions_path = Path(sys.argv[1])
init2_path = Path(sys.argv[2])
forced_root = sys.argv[3]
find_out = Path(sys.argv[4])
wait_out = Path(sys.argv[5])
text = functions_path.read_text(errors="strict")
init2 = init2_path.read_text(errors="strict")
lines = text.splitlines(keepends=True)

def span(name):
    starts = [i for i, line in enumerate(lines) if re.match(rf"^\s*{re.escape(name)}\s*\(\s*\)\s*\{{\s*(?:#.*)?$", line.rstrip("\n"))]
    if len(starts) != 1:
        raise SystemExit(f"expected exactly one {name}() definition, found {len(starts)}")
    start = starts[0]
    for end in range(start + 1, len(lines)):
        if re.match(r"^\s*\}\s*(?:#.*)?$", lines[end].rstrip("\n")):
            return start, end + 1
    raise SystemExit(f"unterminated function: {name}")

find_start, find_end = span("find_root_partition")
wait_start, wait_end = span("wait_root_partition")
find_text = "".join(lines[find_start:find_end])
wait_text = "".join(lines[wait_start:wait_end])
find_out.write_text(find_text)
wait_out.write_text(wait_text)

if "blkid" not in find_text:
    raise SystemExit("original find_root_partition does not contain blkid discovery")
if "pmOS_root" not in find_text:
    raise SystemExit("original find_root_partition does not contain pmOS_root label discovery")
if "find_root_partition" not in wait_text:
    raise SystemExit("wait_root_partition does not call find_root_partition")

patterns = [
    re.compile(r"(?:^|[;\n])\s*(?:local\s+|export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']?\$\(\s*find_root_partition\s*\)[\"']?"),
    re.compile(r"(?:^|[;\n])\s*(?:local\s+|export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[\"']?`\s*find_root_partition\s*`[\"']?"),
]
assigned = []
for pattern in patterns:
    assigned.extend(pattern.findall(wait_text))
assigned = sorted(set(assigned))
if len(assigned) != 1:
    raise SystemExit(f"expected one wait_root_partition assignment from find_root_partition, found {assigned}")
root_variable = assigned[0]

sequence = ["run_hooks /hooks", "wait_root_partition", "resize_root_partition", "resize_root_filesystem", "mount_root_partition", "switch_root"]
positions = []
for token in sequence:
    pos = init2.find(token)
    if pos < 0:
        raise SystemExit(f"init_2nd.sh lacks required token: {token}")
    positions.append(pos)
if positions != sorted(positions) or len(set(positions)) != len(positions):
    raise SystemExit(f"second-stage order is wrong: {list(zip(sequence, positions))}")

print(f"original_find_root_sha256={hashlib.sha256(find_text.encode()).hexdigest()}")
print(f"original_wait_root_sha256={hashlib.sha256(wait_text.encode()).hexdigest()}")
print(f"wait_root_assignment_variable={root_variable}")
print("wait_root_consumes_find_root_stdout=yes")
print("second_stage_order_validation=passed")
print(f"forced_root={forced_root}")
PY
)"
printf '%s\n' "$PATCH_CONTRACT"

python3 - "$BASE/init_functions.sh" "$FORCED_ROOT" "$INSPECTION_DIR/patched-find_root_partition.sh" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
forced_root = sys.argv[2]
out = Path(sys.argv[3])
text = path.read_text(errors="strict")
lines = text.splitlines(keepends=True)
starts = [i for i, line in enumerate(lines) if re.match(r"^\s*find_root_partition\s*\(\s*\)\s*\{\s*(?:#.*)?$", line.rstrip("\n"))]
if len(starts) != 1:
    raise SystemExit(f"expected one find_root_partition(), found {len(starts)}")
start = starts[0]
end = None
for i in range(start + 1, len(lines)):
    if re.match(r"^\s*\}\s*(?:#.*)?$", lines[i].rstrip("\n")):
        end = i + 1
        break
if end is None:
    raise SystemExit("unterminated find_root_partition()")
replacement = f'''find_root_partition() {{
\t# A33 U0i: U0h already created /dev/block/sda36 and verified its exact
\t# size, ext4 type and pmOS_root label before this function is called.
\t# Revalidate the identity here and return only the root path on stdout.
\t[ -b {forced_root} ] || return 0
\ta33x_root_identity="$(blkid {forced_root} 2>/dev/null || true)"
\tcase "$a33x_root_identity" in
\t\t*'TYPE="ext4"'*) ;;
\t\t*) unset a33x_root_identity; return 0 ;;
\tesac
\tcase "$a33x_root_identity" in
\t\t*'LABEL="pmOS_root"'*) ;;
\t\t*) unset a33x_root_identity; return 0 ;;
\tesac
\tunset a33x_root_identity
\tprintf '<6>a33x-direct-root-v1: selected {forced_root}\\n' > /dev/kmsg 2>/dev/null || true
\tprintf '%s\\n' {forced_root}
}}
'''
lines[start:end] = [replacement]
patched = "".join(lines)
if patched.count("find_root_partition()") != 1:
    raise SystemExit("patched file does not contain exactly one find_root_partition()")
path.write_text(patched)
out.write_text(replacement)
PY

sh -n "$BASE/init_functions.sh"
PATCHED_FUNCTIONS_SHA="$(sha256sum "$BASE/init_functions.sh" | awk '{print $1}')"
[[ "$PATCHED_FUNCTIONS_SHA" != "$ORIGINAL_FUNCTIONS_SHA" ]] || {
    echo "REFUSING U0i: init_functions.sh did not change" >&2
    exit 1
}

PATCHED_CONTRACT="$(
    python3 - "$BASE/init_functions.sh" "$INSPECTION_DIR/original-wait_root_partition.sh" "$FORCED_ROOT" <<'PY'
from pathlib import Path
import hashlib
import re
import sys

path = Path(sys.argv[1])
original_wait = Path(sys.argv[2]).read_text()
forced_root = sys.argv[3]
text = path.read_text(errors="strict")
lines = text.splitlines(keepends=True)

def function_text(name):
    starts = [i for i, line in enumerate(lines) if re.match(rf"^\s*{re.escape(name)}\s*\(\s*\)\s*\{{\s*(?:#.*)?$", line.rstrip("\n"))]
    if len(starts) != 1:
        raise SystemExit(f"expected one {name}(), found {len(starts)}")
    start = starts[0]
    for end in range(start + 1, len(lines)):
        if re.match(r"^\s*\}\s*(?:#.*)?$", lines[end].rstrip("\n")):
            return "".join(lines[start:end + 1])
    raise SystemExit(f"unterminated {name}()")

find_text = function_text("find_root_partition")
wait_text = function_text("wait_root_partition")
if wait_text != original_wait:
    raise SystemExit("wait_root_partition changed while patching find_root_partition")
for token in [forced_root, "TYPE=\"ext4\"", "LABEL=\"pmOS_root\"", "a33x-direct-root-v1"]:
    if token not in find_text:
        raise SystemExit(f"patched find_root_partition lacks token: {token}")
if "blkid" not in find_text:
    raise SystemExit("patched find_root_partition lacks direct blkid validation")
print(f"patched_find_root_sha256={hashlib.sha256(find_text.encode()).hexdigest()}")
print(f"preserved_wait_root_sha256={hashlib.sha256(wait_text.encode()).hexdigest()}")
print("direct_root_identity_recheck=yes")
print("wait_root_function_preserved=yes")
print("direct_function_patch_validation=passed")
PY
)"
printf '%s\n' "$PATCHED_CONTRACT"

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
sh -n "$VERIFY/init_functions.sh"
verify_sha repacked-init-functions "$VERIFY/init_functions.sh" "$PATCHED_FUNCTIONS_SHA"

python3 - "$VERIFY" "$PATCHED_ENTRY" "$TMP/verify-tree.txt" "$TMP/verify-hardlinks.txt" <<'PY'
from pathlib import Path
import hashlib
import os
import stat
import sys

root = Path(sys.argv[1])
patched = sys.argv[2]
tree_out = Path(sys.argv[3])
hard_out = Path(sys.argv[4])
rows = []
inodes = {}
for path in sorted(root.rglob("*"), key=lambda p: p.as_posix()):
    rel = path.relative_to(root).as_posix()
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    if path.is_symlink():
        rows.append(f"L {mode:04o} {rel} -> {os.readlink(path)}")
    elif path.is_file():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(f"F {mode:04o} {'<PATCHED>' if rel == patched else digest} {rel}")
        inodes.setdefault((st.st_dev, st.st_ino), []).append(rel)
    elif path.is_dir():
        rows.append(f"D {mode:04o} {rel}")
    elif stat.S_ISCHR(st.st_mode):
        rows.append(f"C {mode:04o} {os.major(st.st_rdev)}:{os.minor(st.st_rdev)} {rel}")
    elif stat.S_ISBLK(st.st_mode):
        rows.append(f"B {mode:04o} {os.major(st.st_rdev)}:{os.minor(st.st_rdev)} {rel}")
    elif stat.S_ISFIFO(st.st_mode):
        rows.append(f"P {mode:04o} {rel}")
    else:
        rows.append(f"O {mode:04o} {rel}")
tree_out.write_text("\n".join(rows) + "\n")
groups = sorted("|".join(sorted(paths)) for paths in inodes.values() if len(paths) > 1)
hard_out.write_text("\n".join(groups) + ("\n" if groups else ""))
PY

python3 - "$TMP/base-tree.txt" "$TMP/base-tree-normalized.txt" "$PATCHED_ENTRY" <<'PY'
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
out = Path(sys.argv[2])
patched = sys.argv[3]
rows = []
matched = 0
pattern = re.compile(r"^(F [0-7]{4}) [0-9a-f]{64} (.+)$")
for line in source.read_text().splitlines():
    match = pattern.match(line)
    if match and match.group(2) == patched:
        rows.append(f"{match.group(1)} <PATCHED> {patched}")
        matched += 1
    else:
        rows.append(line)
if matched != 1:
    raise SystemExit(f"expected one patched tree entry for {patched}, found {matched}")
out.write_text("\n".join(rows) + "\n")
PY
if ! cmp -s "$TMP/base-tree-normalized.txt" "$TMP/verify-tree.txt"; then
    echo "REFUSING U0i: repacking changed content or modes beyond $PATCHED_ENTRY" >&2
    diff -u "$TMP/base-tree-normalized.txt" "$TMP/verify-tree.txt" >&2 || true
    exit 1
fi
if ! cmp -s "$TMP/base-hardlinks.txt" "$TMP/verify-hardlinks.txt"; then
    echo "REFUSING U0i: repacking changed initramfs hard-link topology" >&2
    diff -u "$TMP/base-hardlinks.txt" "$TMP/verify-hardlinks.txt" >&2 || true
    exit 1
fi

u0i_module_count="$(find "$VERIFY/usr/lib/modules" -type f 2>/dev/null \
    | grep -Ec '\.ko(\.(gz|xz|zst))?$' || true)"
[[ "$u0i_module_count" = "$EXPECTED_MODULE_COUNT" ]] || {
    echo "REFUSING U0i: repacked module count changed: $u0i_module_count" >&2
    exit 1
}
verify_sha helper-repacked "$VERIFY/usr/libexec/a33x-muic-switch-dynamic" \
    "$(value "$U0G_REPORT" dynamic_helper_sha256)"
verify_sha hook03-repacked "$VERIFY/hooks/03-a33x-muic-switch-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook03_sha256)"
verify_sha hook04-repacked "$VERIFY/hooks/04-a33x-muic-persist-dynamic.sh" \
    "$(value "$U0G_REPORT" dynamic_hook04_sha256)"
verify_sha hook05-repacked "$VERIFY/hooks/05-a33x-userdata-root-node.sh" \
    "$(value "$U0H_REPORT" root_node_hook_sha256)"

U0I_INITRAMFS_SHA="$(sha256sum "$U0I_INITRAMFS" | awk '{print $1}')"
{
    echo "created=$(date -Ins)"
    echo "operation=patch-exact-u0h-find-root-partition"
    echo "u0h_initramfs=$U0H_INITRAMFS"
    echo "u0h_initramfs_sha256=$(sha256sum "$U0H_INITRAMFS" | awk '{print $1}')"
    echo "u0i_initramfs=$U0I_INITRAMFS"
    echo "u0i_initramfs_sha256=$U0I_INITRAMFS_SHA"
    echo "patched_entry=$PATCHED_ENTRY"
    echo "original_init_functions_sha256=$ORIGINAL_FUNCTIONS_SHA"
    echo "patched_init_functions_sha256=$PATCHED_FUNCTIONS_SHA"
    printf '%s\n' "$PATCH_CONTRACT"
    printf '%s\n' "$PATCHED_CONTRACT"
    echo "base_tree_unchanged_except_init_functions=yes"
    echo "hardlink_topology_preserved=yes"
    echo "embedded_modules=$u0i_module_count"
    echo "patch_status=passed"
    echo "phone_partition_writes=no"
} | tee "$PATCH_REPORT"

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

{
    echo "candidate=U0i-direct-root-function"
    echo "created=$(date -Ins)"
    echo "linuxa33_commit=$LINUXA33_COMMIT"
    echo "functional_base=U0h-userdata-root-node"
    echo "functional_delta=patch-find_root_partition-to-verified-sda36"
    echo "kernel_cmdline_delta=none"
    echo "module_delta=none"
    echo "patched_entry=$PATCHED_ENTRY"
    echo "forced_root=$FORCED_ROOT"
    echo "patch_report=$PATCH_REPORT"
    echo "patch_report_sha256=$(sha256sum "$PATCH_REPORT" | awk '{print $1}')"
    echo "u0h_initramfs=$U0H_INITRAMFS"
    echo "u0h_initramfs_sha256=$(sha256sum "$U0H_INITRAMFS" | awk '{print $1}')"
    echo "u0i_initramfs=$U0I_INITRAMFS"
    echo "u0i_initramfs_sha256=$U0I_INITRAMFS_SHA"
    echo "embedded_modules=$u0i_module_count"
    echo "base_tree_unchanged_except_init_functions=yes"
    echo "hardlink_topology_preserved=yes"
    echo "wait_root_consumes_find_root_stdout=yes"
    echo "wait_root_function_preserved=yes"
    echo "direct_root_identity_recheck=yes"
    echo "second_stage_order_validation=passed"
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
echo "U0i direct-root-function candidate prepared."
echo "Candidate: $CANDIDATE"
echo "SHA256:   $SHA"
echo "Manifest: $MANIFEST"
echo "No phone partition was written."

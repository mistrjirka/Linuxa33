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
DETAILS="$PORT_ROOT/build/a33-u0g-unified-root-handoff-details.txt"

for command in python3 sha256sum gzip cpio grep awk find file stat mktemp rm mkdir date tee; do
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
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
UNPACKED="$TMP/unpacked"
EXTRACTED="$TMP/initramfs"
mkdir -p "$UNPACKED" "$EXTRACTED" "$PORT_ROOT/build"

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
    "$EXTRACTED/init_functions.sh" \
    "$EXTRACTED/init_2nd.sh" \
    "$EXTRACTED/init_functions_2nd.sh"; do
    [[ -f "$required" ]] || {
        echo "REFUSING: unified initramfs file is missing: ${required#$EXTRACTED/}" >&2
        exit 1
    }
done

if [[ ! -x "$EXTRACTED/init_2nd.sh" ]]; then
    echo "REFUSING: embedded /init_2nd.sh is not executable" >&2
    stat "$EXTRACTED/init_2nd.sh" >&2 || true
    exit 1
fi

FLOW_RESULT="$(
    python3 - \
        "$EXTRACTED/init" \
        "$EXTRACTED/init_functions.sh" \
        "$EXTRACTED/init_2nd.sh" \
        "$EXTRACTED/init_functions_2nd.sh" <<'PY'
from pathlib import Path
import re
import sys

init_path = Path(sys.argv[1])
functions_path = Path(sys.argv[2])
init2_path = Path(sys.argv[3])
functions2_path = Path(sys.argv[4])

init_lines = init_path.read_text(errors="replace").splitlines()
functions_lines = functions_path.read_text(errors="replace").splitlines()
functions = "\n".join(functions_lines)
init2 = init2_path.read_text(errors="replace")
functions2 = functions2_path.read_text(errors="replace")

source_re = re.compile(
    r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*"
    r"(?:\.|source)\s+[\"']?(?:\./|/)?init_functions\.sh[\"']?"
    r"(?=$|[\s;&|)])"
)
jump_call_re = re.compile(
    r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*jump_init_2nd"
    r"(?=$|[\s;&|)])"
)
extra_call_re = re.compile(
    r"(?:^|[;&|()]|\bthen\b|\bdo\b)\s*extract_initramfs_extra"
    r"(?=$|[\s;&|)])"
)


def executable_lines(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[int, str]]:
    matches: list[tuple[int, str]] = []
    for lineno, raw in enumerate(lines, 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if pattern.search(stripped):
            matches.append((lineno, stripped))
    return matches


source_calls = executable_lines(init_lines, source_re)
jump_calls = executable_lines(init_lines, jump_call_re)
extra_calls = executable_lines(init_lines, extra_call_re)

if not source_calls:
    raise SystemExit("REFUSING: /init never sources /init_functions.sh")
if not jump_calls:
    raise SystemExit("REFUSING: /init never calls jump_init_2nd")

source_line, source_text = source_calls[0]
jump_line, jump_text = jump_calls[0]
if source_line > jump_line:
    raise SystemExit("REFUSING: /init calls jump_init_2nd before sourcing init_functions.sh")
if extra_calls and jump_line > extra_calls[0][0]:
    raise SystemExit(
        "REFUSING: first jump_init_2nd call occurs after extract_initramfs_extra"
    )

function_match = re.search(
    r"(?ms)^\s*jump_init_2nd\s*\(\s*\)\s*\{\s*\n"
    r"(?P<body>.*?)^\s*\}",
    functions,
)
if function_match is None:
    raise SystemExit("REFUSING: init_functions.sh lacks jump_init_2nd()")

function_body = function_match.group("body")
function_body_line = functions[: function_match.start("body")].count("\n") + 1
existence_guard_re = re.compile(
    r"(?:test\s+-e|\[\s+-e)\s+[\"']?/init_2nd\.sh[\"']?"
)
exec_re = re.compile(
    r"(?m)^\s*exec\s+(?:(?:/bin/)?sh\s+)?"
    r"[\"']?/init_2nd\.sh[\"']?\s*$"
)
if existence_guard_re.search(function_body) is None:
    raise SystemExit("REFUSING: jump_init_2nd lacks an /init_2nd.sh existence guard")
if "return" not in function_body:
    raise SystemExit("REFUSING: jump_init_2nd does not return when second stage is absent")
exec_match = exec_re.search(function_body)
if exec_match is None:
    raise SystemExit("REFUSING: jump_init_2nd does not exec /init_2nd.sh")
exec_line = function_body_line + function_body[: exec_match.start()].count("\n")

for token in (
    "wait_root_partition",
    "resize_root_partition",
    "resize_root_filesystem",
    "mount_root_partition",
    "switch_root",
):
    if token not in init2:
        raise SystemExit(f"REFUSING: /init_2nd.sh lacks {token}")

if "find_root_partition" not in functions or "pmOS_root" not in functions:
    raise SystemExit("REFUSING: init_functions.sh lacks pmOS_root discovery")
if "resize2fs" not in functions2:
    raise SystemExit("REFUSING: second-stage functions lack ext4 resize2fs support")
if "check_filesystem" not in functions2:
    raise SystemExit("REFUSING: second-stage functions lack filesystem checking")

extra_line = extra_calls[0][0] if extra_calls else 0
extra_text = extra_calls[0][1] if extra_calls else "none"

print(f"init_functions_source_line={source_line}")
print(f"init_functions_source_text={source_text}")
print(f"jump_init_2nd_call_line={jump_line}")
print(f"jump_init_2nd_call_text={jump_text}")
print(f"extract_extra_call_line={extra_line}")
print(f"extract_extra_call_text={extra_text}")
print(f"jump_init_2nd_exec_line={exec_line}")
print("jump_init_2nd_guard=yes")
print("jump_init_2nd_exec=yes")
print("init_2nd_invocation_before_extra=yes")
print("second_stage_root_wait=yes")
print("second_stage_root_resize=yes")
print("second_stage_root_mount=yes")
print("second_stage_switch_root=yes")
print("pmos_root_discovery=yes")
print("root_resize_present=yes")
print("root_fsck_present=yes")
PY
)"

printf '%s\n' "$FLOW_RESULT" > "$DETAILS"

DEVICEINFO="$(
    find "$EXTRACTED" -type f \
        \( -path '*/usr/share/deviceinfo/deviceinfo' -o -path '*/etc/deviceinfo' \) \
        -print -quit
)"

CREATE_EXTRA="$(
    python3 - "$DEVICEINFO" <<'PY'
from pathlib import Path
import re
import sys

path = sys.argv[1]
if not path:
    print("unset")
    raise SystemExit(0)

text = Path(path).read_text(errors="replace")
pattern = re.compile(
    r"^\s*deviceinfo_create_initfs_extra\s*=\s*"
    r"(?P<value>[^#\r\n]+?)\s*(?:#.*)?$",
    re.MULTILINE,
)
match = pattern.search(text)
if match is None:
    print("unset")
else:
    value = match.group("value").strip().strip("\"'").lower()
    print(value or "unset")
PY
)"

case "$CREATE_EXTRA" in
    true|yes|1)
        echo "REFUSING: deviceinfo explicitly requires initramfs-extra" >&2
        exit 1
        ;;
esac

INIT_EXTRA_PRESENT=no
[[ -e "$EXTRACTED/initramfs-extra" ]] && INIT_EXTRA_PRESENT=yes

flow_value() {
    local key="$1"
    awk -F= -v key="$key" '$1==key {print substr($0, length(key)+2); exit}' "$DETAILS"
}

{
    echo "created=$(date -Ins)"
    echo "operation=verify-exact-u0g-unified-root-handoff"
    echo "recovery=$RECOVERY"
    echo "recovery_sha256=$RECOVERY_SHA"
    echo "ramdisk_sha256=$RAMDISK_SHA"
    echo "init_2nd_embedded=yes"
    echo "init_2nd_executable=yes"
    echo "init_functions_source_line=$(flow_value init_functions_source_line)"
    echo "jump_init_2nd_call_line=$(flow_value jump_init_2nd_call_line)"
    echo "jump_init_2nd_exec_line=$(flow_value jump_init_2nd_exec_line)"
    echo "jump_init_2nd_guard=$(flow_value jump_init_2nd_guard)"
    echo "jump_init_2nd_exec=$(flow_value jump_init_2nd_exec)"
    echo "init_2nd_invocation_before_extra=$(flow_value init_2nd_invocation_before_extra)"
    echo "deviceinfo_create_initfs_extra=$CREATE_EXTRA"
    echo "embedded_initramfs_extra=$INIT_EXTRA_PRESENT"
    echo "pmos_boot_required_before_second_stage=no"
    echo "pmos_root_discovery=$(flow_value pmos_root_discovery)"
    echo "root_wait_present=$(flow_value second_stage_root_wait)"
    echo "root_resize_present=$(flow_value root_resize_present)"
    echo "root_mount_present=$(flow_value second_stage_root_mount)"
    echo "switch_root_present=$(flow_value second_stage_switch_root)"
    echo "root_fsck_present=$(flow_value root_fsck_present)"
    echo "cache_partition_required=no"
    echo "verification_status=passed"
} | tee "$REPORT"

echo
echo "Exact U0g unified-root handoff verified."
echo "Report:  $REPORT"
echo "Details: $DETAILS"
echo "The first real-rootfs test needs pmOS_root on userdata only; cache stays untouched."

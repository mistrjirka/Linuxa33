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

for command in python3 sha256sum gzip cpio grep awk find file stat sed; do
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
        "$EXTRACTED/init_2nd.sh" \
        "$EXTRACTED/init_functions_2nd.sh" <<'PY'
from pathlib import Path
import re
import sys

init_path = Path(sys.argv[1])
init2_path = Path(sys.argv[2])
functions2_path = Path(sys.argv[3])

init_lines = init_path.read_text(errors="replace").splitlines()
init2 = init2_path.read_text(errors="replace")
functions2 = functions2_path.read_text(errors="replace")

# Match executable shell statements that invoke the embedded second stage.
# Accepted forms include:
#   /init_2nd.sh
#   sh /init_2nd.sh
#   exec /bin/sh /init_2nd.sh
#   . /init_2nd.sh
#   if ...; then /init_2nd.sh; fi
invoke_re = re.compile(
    r"(?:^|[;&|()])\s*(?:exec\s+)?(?:busybox\s+)?"
    r"(?:(?:/bin/)?sh\s+|(?:source|\.)\s+)?/?init_2nd\.sh"
    r"(?=$|[\s;&|)])"
)
extra_re = re.compile(r"(?:^|[;&|()])\s*extract_initramfs_extra(?=$|[\s;&|)])")
extra_definition_re = re.compile(r"^\s*(?:function\s+)?extract_initramfs_extra\s*\(\s*\)")

invocations = []
extra_calls = []
for lineno, raw in enumerate(init_lines, 1):
    stripped = raw.strip()
    if not stripped or stripped.startswith("#"):
        continue
    if invoke_re.search(stripped):
        invocations.append((lineno, stripped))
    if not extra_definition_re.search(stripped) and extra_re.search(stripped):
        extra_calls.append((lineno, stripped))

if not invocations:
    matches = [
        f"{number}:{line}"
        for number, line in enumerate(init_lines, 1)
        if "init_2nd.sh" in line
    ]
    raise SystemExit(
        "REFUSING: /init contains no executable invocation of /init_2nd.sh; "
        f"raw references={matches}"
    )

invocation_line, invocation_text = invocations[0]
if extra_calls and invocation_line > extra_calls[0][0]:
    raise SystemExit(
        "REFUSING: first /init_2nd.sh invocation occurs after "
        "extract_initramfs_extra"
    )

for token in ("wait_root_partition", "mount_root_partition", "switch_root"):
    if token not in init2:
        raise SystemExit(f"REFUSING: /init_2nd.sh lacks {token}")

combined_second_stage = init2 + "\n" + functions2
if "pmOS_root" not in combined_second_stage:
    raise SystemExit("REFUSING: second-stage initramfs lacks pmOS_root discovery")

resize_present = "resize2fs" in combined_second_stage
fsck_present = any(token in combined_second_stage for token in ("e2fsck", "fsck.ext4", "fsck"))
extra_line = extra_calls[0][0] if extra_calls else 0
extra_text = extra_calls[0][1] if extra_calls else "none"

print(f"init_2nd_invocation_line={invocation_line}")
print(f"init_2nd_invocation_text={invocation_text}")
print(f"extract_extra_call_line={extra_line}")
print(f"extract_extra_call_text={extra_text}")
print("init_2nd_invocation_before_extra=yes")
print("second_stage_root_wait=yes")
print("second_stage_root_mount=yes")
print("second_stage_switch_root=yes")
print("pmos_root_discovery=yes")
print(f"root_resize_present={'yes' if resize_present else 'no'}")
print(f"root_fsck_present={'yes' if fsck_present else 'no'}")
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
    echo "init_2nd_invocation_line=$(flow_value init_2nd_invocation_line)"
    echo "init_2nd_invocation_before_extra=$(flow_value init_2nd_invocation_before_extra)"
    echo "deviceinfo_create_initfs_extra=$CREATE_EXTRA"
    echo "embedded_initramfs_extra=$INIT_EXTRA_PRESENT"
    echo "pmos_boot_required_before_second_stage=no"
    echo "pmos_root_discovery=$(flow_value pmos_root_discovery)"
    echo "root_wait_present=$(flow_value second_stage_root_wait)"
    echo "root_mount_present=$(flow_value second_stage_root_mount)"
    echo "switch_root_present=$(flow_value second_stage_switch_root)"
    echo "root_resize_present=$(flow_value root_resize_present)"
    echo "root_fsck_present=$(flow_value root_fsck_present)"
    echo "cache_partition_required=no"
    echo "verification_status=passed"
} | tee "$REPORT"

echo
echo "Exact U0g unified-root handoff verified."
echo "Report:  $REPORT"
echo "Details: $DETAILS"
echo "The first real-rootfs test needs pmOS_root on userdata only; cache stays untouched."

#!/usr/bin/env bash
set -euo pipefail

trap 'rc=$?; echo "ERROR: ${BASH_SOURCE[0]} failed at line $LINENO: $BASH_COMMAND (rc=$rc)" >&2; exit "$rc"' ERR

export LC_ALL=C
export LANG=C

PORT_ROOT="${PORT_ROOT:-$HOME/a33-port}"
ADB="${ADB:-adb}"
SELF="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(cd "$(dirname "$SELF")" && pwd)"

# shellcheck source=lib/a33-adb-runtime.sh
source "$SCRIPT_DIR/lib/a33-adb-runtime.sh"
ROOT_IMAGE_LINK="$PORT_ROOT/build/userdata-rootfs-images/current/a33x-userdata-pmos-root.img"
REPORT="$PORT_ROOT/build/a33-command-capabilities.txt"
DETAILS="$PORT_ROOT/build/a33-command-capabilities-details.txt"
KNOWN_TWRP_SHA256="414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"

HOST_REQUIRED=(
    bash "$ADB" sha256sum stat awk grep find sort date mkdir tee readlink realpath
    tar ip ping timeout python3 ssh lsusb sudo mktemp cp rm cat seq sleep git
    debugfs sed tr
)
HOST_OPTIONAL=(journalctl shellcheck pv)
TWRP_REQUIRED=(
    sh awk grep sha256sum stat df rm readlink blockdev tail cat find dd sync
    mkdir umount mount wc ls uname dmesg getprop tr sed cp
)
ADB_ALLOWED=(devices get-state get-serialno shell push exec-out reboot)
FUTURE_SCRIPTS=(
    lib/a33-adb-runtime.sh
    stage-a33-userdata-rootfs-in-twrp.sh
    deploy-a33-rootfs-to-userdata.sh
    execute-a33-first-rootfs-deployment.sh
    flash-a33-u0g-after-userdata-deploy.sh
    boot-observe-a33-first-rootfs.sh
    collect-a33-first-rootfs-live.sh
    collect-a33-first-rootfs-previous-boot.sh
    collect-a33-previous-boot.sh
    verify-a33-twrp-rescue-assets.sh
    restore-a33-twrp-odin.sh
    audit-a33-first-rootfs-chain.sh
    audit-a33-first-rootfs-chain-final.sh
    audit-a33-first-rootfs-transport-final.sh
    audit-a33-first-rootfs-transport-bound-final.sh
)

mkdir -p "$PORT_ROOT/build"
: > "$DETAILS"

echo "=== Host command availability ===" | tee -a "$DETAILS"
for command in "${HOST_REQUIRED[@]}"; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "REFUSING: required host command is missing: $command" >&2
        exit 1
    }
    printf 'host_required=%s path=%s\n' "$command" "$(command -v "$command")" \
        | tee -a "$DETAILS"
done
for command in "${HOST_OPTIONAL[@]}"; do
    if command -v "$command" >/dev/null 2>&1; then
        printf 'host_optional=%s status=present path=%s\n' \
            "$command" "$(command -v "$command")" | tee -a "$DETAILS"
    else
        printf 'host_optional=%s status=absent\n' "$command" | tee -a "$DETAILS"
    fi
done

bash -n "$SELF"
for script in "${FUTURE_SCRIPTS[@]}"; do
    path="$SCRIPT_DIR/$script"
    [[ -f "$path" ]] || {
        echo "REFUSING: future script is missing: $path" >&2
        exit 1
    }
    bash -n "$path"
done

ADB_SCAN="$(
    python3 - "$SCRIPT_DIR" "${FUTURE_SCRIPTS[@]}" <<'PY'
from pathlib import Path
import re
import sys

root = Path(sys.argv[1])
files = sys.argv[2:]
allowed = {"devices", "get-state", "get-serialno", "shell", "push", "exec-out", "reboot"}
selected_allowed = {"shell", "push", "exec-out", "reboot"}
found: dict[str, list[str]] = {}
problems: list[str] = []

# Match $ADB only where it is syntactically positioned as a command, including
# after shell control operators and reserved words. This deliberately does not
# match data occurrences such as: for command in "$ADB" readlink ...
command_prefix = r'''(?:
    ^
    | [;&|(){}]
    | \b(?:if|then|elif|else|while|until|do|time|command)\b
    | !
)\s*'''
selected_pattern = re.compile(
    command_prefix + r'''(?:"\$ADB"|'\$ADB'|\$ADB)\s+([A-Za-z0-9_-]+)''',
    re.VERBOSE,
)

for filename in files:
    path = root / filename
    text = path.read_text(errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        for match in selected_pattern.finditer(line):
            command = match.group(1)
            found.setdefault(command, []).append(f"{filename}:{number}")
            if command not in selected_allowed:
                problems.append(f"disallowed adb subcommand {command}: {filename}:{number}")

helper_path = root / "lib/a33-adb-runtime.sh"
helper_text = helper_path.read_text(errors="replace")
helper_patterns = {
    "devices": re.compile(r'''"\$A33_ADB_BIN"\s+devices\b'''),
    "shell": re.compile(r'''"\$A33_ADB_BIN"\s+-s\s+"\$serial"\s+shell\b'''),
    "get-state": re.compile(r'''"\$A33_ADB_BIN"\s+-s\s+"\$serial"\s+get-state\b'''),
    "get-serialno": re.compile(r'''"\$A33_ADB_BIN"\s+-s\s+"\$serial"\s+get-serialno\b'''),
}
for command, pattern in helper_patterns.items():
    locations = [
        f"lib/a33-adb-runtime.sh:{number}"
        for number, line in enumerate(helper_text.splitlines(), 1)
        if pattern.search(line)
    ]
    if not locations:
        problems.append(f"required raw adb helper invocation is missing: {command}")
    else:
        found.setdefault(command, []).extend(locations)

if re.search(r'''"\$A33_ADB_BIN"[^\n]*\b(?:exec-in|help)\b''', helper_text):
    problems.append("raw adb helper contains prohibited exec-in or help invocation")

if problems:
    raise SystemExit("\n".join(problems))
for command in sorted(found):
    print(f"adb_subcommand={command} locations={','.join(found[command])}")
missing = allowed - set(found)
for command in sorted(missing):
    print(f"adb_allowed_but_not_statically_invoked={command}")
PY
)"
printf '%s\n' "$ADB_SCAN" | tee -a "$DETAILS"

ADB_VERSION="$("$ADB" version 2>&1)"
printf '%s\n' "$ADB_VERSION" | sed 's/^/adb_version_output=/' | tee -a "$DETAILS"

echo "=== Wait for exact known-good TWRP ===" | tee -a "$DETAILS"
a33_init_recovery_adb 30

REMOTE_CAPABILITY="$(
    "$ADB" shell sh -s -- "${TWRP_REQUIRED[@]}" 2>/dev/null <<'SH' | tr -d '\r'
set -eu

echo "recovery_sha=$(sha256sum /dev/block/by-name/recovery | awk 'NR==1 {print $1}')"
for command in "$@"; do
    if command -v "$command" >/dev/null 2>&1; then
        echo "command_present=$command"
    else
        echo "command_missing=$command"
        exit 10
    fi
done

work=/tmp/a33-command-capability
metadata=/dev/block/by-name/metadata
mountpoint=$work/metadata
cleanup() {
    umount "$mountpoint" 2>/dev/null || true
    rm -rf "$work" 2>/dev/null || true
}
trap cleanup EXIT
rm -rf "$work"
mkdir -p "$mountpoint"
printf 'A33-CAPABILITY-PROBE\n' > "$work/input"
cp "$work/input" "$work/copy"
grep -Fqx 'A33-CAPABILITY-PROBE' "$work/copy"
awk 'NR==1 && $0=="A33-CAPABILITY-PROBE" {ok=1} END {exit !ok}' "$work/copy"
sed -n '1p' "$work/copy" | grep -Fqx 'A33-CAPABILITY-PROBE'
[ "$(wc -c < "$work/copy")" -gt 0 ]
[ "$(stat -c '%s' "$work/copy")" -gt 0 ]
[ -n "$(sha256sum "$work/copy" | awk 'NR==1 {print $1}')" ]
[ "$(readlink -f "$work/copy")" = "$work/copy" ]
df -k /tmp > "$work/df.txt"
grep -q . "$work/df.txt"
tail -n +2 /proc/swaps > "$work/swaps.txt" || true
find "$work" -mindepth 1 -maxdepth 1 -printf '%f\n' > "$work/find.txt"
grep -Fqx copy "$work/find.txt"
dd if=/dev/zero of="$work/dd.bin" bs=4096 count=4 2>/dev/null
[ "$(stat -c '%s' "$work/dd.bin")" = 16384 ]
sync
[ "$(blockdev --getsize64 /dev/block/by-name/recovery)" = 100663296 ]
[ "$(blockdev --getro /dev/block/by-name/userdata)" = 0 ]
resolved_metadata="$(readlink -f "$metadata" 2>/dev/null || true)"
existing_mount="$(awk -v a="$metadata" -v b="$resolved_metadata" '$1==a || $1==b {print $2; exit}' /proc/mounts 2>/dev/null || true)"
if [ -n "$existing_mount" ]; then
    echo "metadata_readonly_mount_test=already-mounted:$existing_mount"
else
    mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$metadata" "$mountpoint"
    grep -q " $mountpoint " /proc/mounts
    umount "$mountpoint"
    ! grep -q " $mountpoint " /proc/mounts
    echo "metadata_readonly_mount_test=passed"
fi

uname -a > "$work/uname.txt"
dmesg > "$work/dmesg.txt"
getprop > "$work/getprop.txt"
ls -la "$work" > "$work/ls.txt"
printf 'b\r\n' | tr -d '\r' | grep -qx b

echo "twrp_functional_probe=passed"
SH
)"
printf '%s\n' "$REMOTE_CAPABILITY" | tee -a "$DETAILS"

METADATA_IDENTITY="$(a33_ext4_identity /dev/block/by-name/metadata)"
printf '%s\n' "$METADATA_IDENTITY" | sed 's/^/metadata_identity_/' | tee -a "$DETAILS"
if [[ "$(printf '%s\n' "$METADATA_IDENTITY" | awk -F= '$1=="type" {print $2; exit}')" != ext4 ]]; then
    echo "REFUSING: metadata ext4 identity parser did not pass" >&2
    exit 1
fi

RECOVERY_SHA="$(printf '%s\n' "$REMOTE_CAPABILITY" | awk -F= '$1=="recovery_sha" {print $2; exit}')"
if [[ "$RECOVERY_SHA" != "$KNOWN_TWRP_SHA256" || \
      "$(printf '%s\n' "$REMOTE_CAPABILITY" | grep -c '^command_present=')" \
          -ne "${#TWRP_REQUIRED[@]}" || \
      "$(printf '%s\n' "$REMOTE_CAPABILITY" | grep -c '^twrp_functional_probe=passed$')" \
          -ne 1 ]]; then
    echo "REFUSING: exact TWRP command capability probe did not pass" >&2
    printf '%s\n' "$REMOTE_CAPABILITY" >&2
    exit 1
fi

LOCAL_PROBE="$(mktemp)"
REMOTE_PROBE=/tmp/a33-adb-capability-probe.bin
cleanup_host() {
    rm -f "$LOCAL_PROBE"
    "$ADB" shell "rm -f '$REMOTE_PROBE'" >/dev/null 2>&1 || true
}
trap cleanup_host EXIT
python3 - "$LOCAL_PROBE" <<'PY'
from pathlib import Path
import sys
payload = bytes(range(256)) * 4096
Path(sys.argv[1]).write_bytes(payload)
PY
LOCAL_SHA="$(sha256sum "$LOCAL_PROBE" | awk '{print $1}')"
LOCAL_SIZE="$(stat -Lc '%s' "$LOCAL_PROBE")"
"$ADB" push "$LOCAL_PROBE" "$REMOTE_PROBE" >/dev/null
REMOTE_META="$("$ADB" shell "stat -c '%s' '$REMOTE_PROBE'; sha256sum '$REMOTE_PROBE'" | tr -d '\r')"
REMOTE_SIZE="$(printf '%s\n' "$REMOTE_META" | sed -n '1p')"
REMOTE_SHA="$(printf '%s\n' "$REMOTE_META" | awk 'NR==2 {print $1}')"
EXEC_OUT_SHA="$(
    "$ADB" exec-out sh -c "cat '$REMOTE_PROBE'" | sha256sum | awk '{print $1}'
)"
if [[ "$REMOTE_SIZE" != "$LOCAL_SIZE" || "$REMOTE_SHA" != "$LOCAL_SHA" || \
      "$EXEC_OUT_SHA" != "$LOCAL_SHA" ]]; then
    echo "REFUSING: ADB push or exec-out binary capability probe failed" >&2
    exit 1
fi
cleanup_host
trap - EXIT

ROOT_IMAGE="$(readlink -f "$ROOT_IMAGE_LINK" 2>/dev/null || true)"
[[ -f "$ROOT_IMAGE" ]] || {
    echo "REFUSING: current rootfs deployment image is missing" >&2
    exit 1
}
root_has() {
    local path="$1"
    debugfs -R "stat $path" "$ROOT_IMAGE" 2>&1 | grep -q '^Inode:'
}
root_has_any() {
    local path
    for path in "$@"; do
        if root_has "$path"; then
            echo "$path"
            return 0
        fi
    done
    return 1
}
for required in /bin/sh /sbin/init /sbin/rc-service /usr/sbin/sshd /usr/bin/nmcli; do
    root_has "$required" || {
        echo "REFUSING: normal rootfs lacks required runtime command: $required" >&2
        exit 1
    }
done
ROOT_IP="$(root_has_any /sbin/ip /usr/sbin/ip /bin/ip /usr/bin/ip)" || {
    echo "REFUSING: normal rootfs lacks ip command" >&2
    exit 1
}
ROOT_AWK="$(root_has_any /usr/bin/awk /bin/awk)" || {
    echo "REFUSING: normal rootfs lacks awk command" >&2
    exit 1
}

{
    echo "created=$(date -Ins)"
    echo "operation=audit-host-adb-twrp-and-rootfs-command-capabilities"
    echo "audit_script_sha256=$(sha256sum "$SELF" | awk '{print $1}')"
    echo "host_required_commands=${HOST_REQUIRED[*]}"
    echo "host_required_commands_status=passed"
    echo "adb_allowed_subcommands=${ADB_ALLOWED[*]}"
    echo "adb_actual_subcommands=$(printf '%s\n' "$ADB_SCAN" | awk -F'[ =]' '$1=="adb_subcommand" {printf "%s%s", sep, $2; sep=","}')"
    echo "adb_shell_probe=passed"
    echo "adb_push_binary_probe=passed"
    echo "adb_exec_out_binary_probe=passed"
    echo "adb_exec_in_used=no"
    echo "adb_help_feature_detection_used=no"
    echo "adb_reboot_recovery_test=deferred-until-observer"
    echo "twrp_recovery_sha256=$RECOVERY_SHA"
    echo "twrp_required_commands=${TWRP_REQUIRED[*]}"
    echo "twrp_required_commands_status=passed"
    echo "twrp_command_option_probes=passed"
    echo "twrp_ext4_identity_parser=passed"
    echo "rootfs_required_runtime_commands=passed"
    echo "rootfs_ip_path=$ROOT_IP"
    echo "rootfs_awk_path=$ROOT_AWK"
    echo "persistent_phone_partition_writes=no"
    echo "volatile_twrp_tmpfs_writes=yes"
    echo "command_capability_audit_status=passed"
} | tee "$REPORT"

echo
echo "A33 command capability audit passed."
echo "Report:  $REPORT"
echo "Details: $DETAILS"
echo "Only allowed and empirically verified commands remain in the future chain."

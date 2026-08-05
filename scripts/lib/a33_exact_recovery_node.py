from __future__ import annotations

from dataclasses import dataclass


PARTNAME = "recovery"
EXPECTED_BYTES = "100663296"
TEMP_NODE = "/tmp/a33x-exact-recovery.block"


class ExactRecoveryNodeError(RuntimeError):
    pass


PREPARE_SCRIPT = r'''set -eu
partname="$1"
expected_bytes="$2"
expected_sha="$3"
node="$4"
created=no
success=no

cleanup_failed_prepare()
{
    [ "$success" = yes ] && return 0
    [ "$created" = no ] || rm -f "$node" 2>/dev/null || true
}
trap cleanup_failed_prepare EXIT

for command in awk basename blockdev cat chmod grep mknod readlink rm sha256sum stat; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_command=$command"
        exit 40
    }
done

matches=""
match_count=0
for uevent in /sys/class/block/*/uevent; do
    [ -f "$uevent" ] || continue
    if grep -Fqx "PARTNAME=$partname" "$uevent" 2>/dev/null; then
        sysfs="${uevent%/uevent}"
        matches="$matches $sysfs"
        match_count=$((match_count + 1))
    fi
done
[ "$match_count" -eq 1 ] || {
    echo "partname_match_count=$match_count partname=$partname matches=$matches"
    exit 41
}
sysfs="${matches# }"
kernel_name="${sysfs##*/}"
kernel_dev="$(cat "$sysfs/dev" 2>/dev/null || true)"
case "$kernel_dev" in
    [0-9]*:[0-9]*) ;;
    *) echo "invalid_kernel_dev=$kernel_dev"; exit 42 ;;
esac
major="${kernel_dev%:*}"
minor="${kernel_dev#*:}"
case "$major:$minor" in
    *[!0-9:]*|:|*:) echo "invalid_major_minor=$major:$minor"; exit 43 ;;
esac
sysfs_resolved="$(readlink -f "/sys/dev/block/$kernel_dev" 2>/dev/null || true)"
[ "$(basename "$sysfs_resolved" 2>/dev/null || true)" = "$kernel_name" ] || {
    echo "kernel_name_mismatch kernel_name=$kernel_name resolved=$sysfs_resolved"
    exit 44
}

if [ -e "$node" ] || [ -L "$node" ]; then
    [ -b "$node" ] || {
        echo "unsafe_existing_node=$node"
        exit 45
    }
else
    mknod "$node" b "$major" "$minor"
    chmod 600 "$node"
    created=yes
fi
expected_hex="$(printf '%x:%x' "$major" "$minor")"
actual_hex="$(stat -c '%t:%T' "$node" 2>/dev/null || true)"
[ "$actual_hex" = "$expected_hex" ] || {
    echo "node_device_mismatch actual=$actual_hex expected=$expected_hex"
    exit 46
}
bytes="$(blockdev --getsize64 "$node" 2>/dev/null || true)"
readonly="$(blockdev --getro "$node" 2>/dev/null || true)"
[ "$bytes" = "$expected_bytes" ] || {
    echo "node_size_mismatch actual=$bytes expected=$expected_bytes"
    exit 47
}
[ "$readonly" = 0 ] || {
    echo "node_readonly_mismatch actual=$readonly expected=0"
    exit 48
}

mount_users="$(awk -v node="$node" -v kernel="$kernel_name" '
    {
        source=$1
        command="readlink -f \"" source "\" 2>/dev/null"
        command | getline resolved
        close(command)
        if (source == node || resolved == node || resolved ~ ("/" kernel "$")) print source " " $2
    }
' /proc/mounts 2>/dev/null || true)"
swap_users="$(awk -v node="$node" -v kernel="$kernel_name" 'NR > 1 {
    source=$1
    command="readlink -f \"" source "\" 2>/dev/null"
    command | getline resolved
    close(command)
    if (source == node || resolved == node || resolved ~ ("/" kernel "$")) print source
}' /proc/swaps 2>/dev/null || true)"
dm_users=""
for dm in /sys/block/dm-*; do
    [ -d "$dm/slaves" ] || continue
    if [ -e "$dm/slaves/$kernel_name" ]; then
        dm_users="$dm_users ${dm##*/}"
    fi
done
[ -z "$mount_users" ] && [ -z "$swap_users" ] && [ -z "$dm_users" ] || {
    echo "recovery_active mount_users=$mount_users swap_users=$swap_users dm_users=$dm_users"
    exit 49
}

actual_sha="$(sha256sum "$node" 2>/dev/null | awk 'NR==1 {print $1}')"
[ "$actual_sha" = "$expected_sha" ] || {
    echo "recovery_sha_mismatch actual=$actual_sha expected=$expected_sha"
    exit 50
}

success=yes
echo "exact_recovery_node=$node"
echo "exact_recovery_node_created=$created"
echo "exact_recovery_partname=$partname"
echo "exact_recovery_kernel_name=$kernel_name"
echo "exact_recovery_kernel_dev=$kernel_dev"
echo "exact_recovery_sysfs=$sysfs"
echo "exact_recovery_sysfs_resolved=$sysfs_resolved"
echo "exact_recovery_node_hex_dev=$actual_hex"
echo "exact_recovery_bytes=$bytes"
echo "exact_recovery_readonly=$readonly"
echo "exact_recovery_sha256=$actual_sha"
echo "exact_recovery_active_users=none"
echo "exact_recovery_node_status=passed"
'''


CLEANUP_SCRIPT = r'''set -eu
node="$1"
created="$2"
kernel_dev="$3"
if [ "$created" = yes ]; then
    [ -b "$node" ] || {
        echo "cleanup_node_missing=$node"
        exit 60
    }
    major="${kernel_dev%:*}"
    minor="${kernel_dev#*:}"
    expected_hex="$(printf '%x:%x' "$major" "$minor")"
    actual_hex="$(stat -c '%t:%T' "$node" 2>/dev/null || true)"
    [ "$actual_hex" = "$expected_hex" ] || {
        echo "cleanup_node_device_mismatch actual=$actual_hex expected=$expected_hex"
        exit 61
    }
    rm -f "$node"
    [ ! -e "$node" ] && [ ! -L "$node" ] || exit 62
    echo "exact_recovery_node_cleanup=removed-created-node"
else
    echo "exact_recovery_node_cleanup=preserved-existing-node"
fi
echo "exact_recovery_node_cleanup_status=passed"
'''


@dataclass(frozen=True)
class ExactRecoveryNodeState:
    node: str
    created: bool
    partname: str
    kernel_name: str
    kernel_dev: str
    sysfs: str
    sysfs_resolved: str
    node_hex_dev: str
    bytes: str
    readonly: str
    sha256: str


def parse_values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.replace("\r", "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            result.setdefault(key, value)
    return result


def state_from_output(output: str, expected_sha: str) -> ExactRecoveryNodeState:
    values = parse_values(output)
    expected = {
        "exact_recovery_node": TEMP_NODE,
        "exact_recovery_partname": PARTNAME,
        "exact_recovery_bytes": EXPECTED_BYTES,
        "exact_recovery_readonly": "0",
        "exact_recovery_sha256": expected_sha,
        "exact_recovery_active_users": "none",
        "exact_recovery_node_status": "passed",
    }
    failures = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    if values.get("exact_recovery_node_created") not in {"yes", "no"}:
        failures.append("exact_recovery_node_created is invalid")
    for key in (
        "exact_recovery_kernel_name",
        "exact_recovery_kernel_dev",
        "exact_recovery_sysfs",
        "exact_recovery_sysfs_resolved",
        "exact_recovery_node_hex_dev",
    ):
        if not values.get(key):
            failures.append(f"{key} is missing")
    if failures:
        raise ExactRecoveryNodeError(
            "exact recovery-node preparation contract failed:\n" + "\n".join(failures)
        )
    return ExactRecoveryNodeState(
        node=values["exact_recovery_node"],
        created=values["exact_recovery_node_created"] == "yes",
        partname=values["exact_recovery_partname"],
        kernel_name=values["exact_recovery_kernel_name"],
        kernel_dev=values["exact_recovery_kernel_dev"],
        sysfs=values["exact_recovery_sysfs"],
        sysfs_resolved=values["exact_recovery_sysfs_resolved"],
        node_hex_dev=values["exact_recovery_node_hex_dev"],
        bytes=values["exact_recovery_bytes"],
        readonly=values["exact_recovery_readonly"],
        sha256=values["exact_recovery_sha256"],
    )


def prepare(common, adb: str, serial: str, expected_sha: str) -> ExactRecoveryNodeState:
    output = common.adb_shell(
        adb,
        serial,
        PREPARE_SCRIPT,
        PARTNAME,
        EXPECTED_BYTES,
        expected_sha,
        TEMP_NODE,
    )
    return state_from_output(output, expected_sha)


def cleanup(common, adb: str, serial: str, state: ExactRecoveryNodeState) -> str:
    output = common.adb_shell(
        adb,
        serial,
        CLEANUP_SCRIPT,
        state.node,
        "yes" if state.created else "no",
        state.kernel_dev,
    )
    if output.count("exact_recovery_node_cleanup_status=passed") != 1:
        raise ExactRecoveryNodeError("exact recovery-node cleanup did not report success")
    return output

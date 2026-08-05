from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


EXACT_NODE = "/dev/block/sda36"
EXACT_SYSFS = "/sys/class/block/sda36"
EXACT_KERNEL_NAME = "sda36"
EXACT_BYTES = "114240258048"


class ExactBlockNodeError(RuntimeError):
    pass


PREPARE_SCRIPT = r'''set -eu
node="$1"
sysfs="$2"
expected_name="$3"
expected_bytes="$4"
created=no
parent_created=no
success=no

cleanup_failed_prepare()
{
    [ "$success" = yes ] && return 0
    [ "$created" = no ] || rm -f "$node" 2>/dev/null || true
    [ "$parent_created" = no ] || rmdir "${node%/*}" 2>/dev/null || true
}
trap cleanup_failed_prepare EXIT

for command in cat readlink basename mknod chmod stat blockdev mkdir rm rmdir printf; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_command=$command"
        exit 40
    }
done

[ -d "$sysfs" ] || {
    echo "sysfs_state=missing path=$sysfs"
    exit 41
}
kernel_dev="$(cat "$sysfs/dev" 2>/dev/null || true)"
case "$kernel_dev" in
    [0-9]*:[0-9]*) ;;
    *)
        echo "invalid_kernel_dev=$kernel_dev"
        exit 42
        ;;
esac
major="${kernel_dev%:*}"
minor="${kernel_dev#*:}"
case "$major:$minor" in
    *[!0-9:]*|:|*:)
        echo "invalid_major_minor=$major:$minor"
        exit 43
        ;;
esac
sysfs_resolved="$(readlink -f "/sys/dev/block/$kernel_dev" 2>/dev/null || true)"
kernel_name="$(basename "$sysfs_resolved" 2>/dev/null || true)"
[ "$kernel_name" = "$expected_name" ] || {
    echo "kernel_name_mismatch actual=$kernel_name expected=$expected_name resolved=$sysfs_resolved"
    exit 44
}

parent="${node%/*}"
if [ ! -d "$parent" ]; then
    mkdir -p "$parent"
    parent_created=yes
fi
if [ -e "$node" ] || [ -L "$node" ]; then
    [ -b "$node" ] || {
        echo "node_state=unsafe-existing path=$node"
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
node_resolved="$(readlink -f "$node" 2>/dev/null || true)"
[ "$node_resolved" = "$node" ] || {
    echo "node_resolution_mismatch actual=$node_resolved expected=$node"
    exit 47
}
bytes="$(blockdev --getsize64 "$node" 2>/dev/null || true)"
[ "$bytes" = "$expected_bytes" ] || {
    echo "node_size_mismatch actual=$bytes expected=$expected_bytes"
    exit 48
}
readonly="$(blockdev --getro "$node" 2>/dev/null || true)"
[ "$readonly" = 0 ] || {
    echo "node_readonly_mismatch actual=$readonly expected=0"
    exit 49
}

success=yes
echo "exact_node=$node"
echo "exact_node_resolved=$node_resolved"
echo "exact_node_created=$created"
echo "exact_parent_created=$parent_created"
echo "exact_sysfs=$sysfs"
echo "exact_sysfs_resolved=$sysfs_resolved"
echo "exact_kernel_name=$kernel_name"
echo "exact_kernel_dev=$kernel_dev"
echo "exact_node_hex_dev=$actual_hex"
echo "exact_node_bytes=$bytes"
echo "exact_node_readonly=$readonly"
echo "exact_block_node_status=passed"
'''


CLEANUP_SCRIPT = r'''set -eu
node="$1"
created="$2"
parent_created="$3"
kernel_dev="$4"

if [ "$created" = yes ]; then
    [ -b "$node" ] || {
        echo "cleanup_node_state=missing-or-not-block path=$node"
        exit 50
    }
    major="${kernel_dev%:*}"
    minor="${kernel_dev#*:}"
    expected_hex="$(printf '%x:%x' "$major" "$minor")"
    actual_hex="$(stat -c '%t:%T' "$node" 2>/dev/null || true)"
    [ "$actual_hex" = "$expected_hex" ] || {
        echo "cleanup_node_device_mismatch actual=$actual_hex expected=$expected_hex"
        exit 51
    }
    rm -f "$node"
    [ ! -e "$node" ] && [ ! -L "$node" ] || {
        echo "cleanup_node_remove_failed path=$node"
        exit 52
    }
    echo "exact_node_cleanup=removed-created-node"
else
    echo "exact_node_cleanup=preserved-existing-node"
fi

if [ "$parent_created" = yes ]; then
    rmdir "${node%/*}" 2>/dev/null || true
fi
echo "exact_block_node_cleanup_status=passed"
'''


@dataclass(frozen=True)
class ExactBlockNodeState:
    node: str
    resolved: str
    created: bool
    parent_created: bool
    sysfs: str
    sysfs_resolved: str
    kernel_name: str
    kernel_dev: str
    node_hex_dev: str
    bytes: str
    readonly: str


def parse_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.replace("\r", "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key, value)
    return values


def state_from_output(output: str) -> ExactBlockNodeState:
    values = parse_values(output)
    expected = {
        "exact_node": EXACT_NODE,
        "exact_node_resolved": EXACT_NODE,
        "exact_sysfs": EXACT_SYSFS,
        "exact_kernel_name": EXACT_KERNEL_NAME,
        "exact_node_bytes": EXACT_BYTES,
        "exact_node_readonly": "0",
        "exact_block_node_status": "passed",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for key in (
        "exact_sysfs_resolved",
        "exact_kernel_dev",
        "exact_node_hex_dev",
    ):
        if not values.get(key):
            mismatches.append(f"{key}: missing")
    for key in ("exact_node_created", "exact_parent_created"):
        if values.get(key) not in {"yes", "no"}:
            mismatches.append(f"{key}: invalid={values.get(key)!r}")
    if mismatches:
        raise ExactBlockNodeError(
            "exact block-node preparation contract failed:\n" + "\n".join(mismatches)
        )
    return ExactBlockNodeState(
        node=values["exact_node"],
        resolved=values["exact_node_resolved"],
        created=values["exact_node_created"] == "yes",
        parent_created=values["exact_parent_created"] == "yes",
        sysfs=values["exact_sysfs"],
        sysfs_resolved=values["exact_sysfs_resolved"],
        kernel_name=values["exact_kernel_name"],
        kernel_dev=values["exact_kernel_dev"],
        node_hex_dev=values["exact_node_hex_dev"],
        bytes=values["exact_node_bytes"],
        readonly=values["exact_node_readonly"],
    )


def prepare(common, adb: str, serial: str) -> ExactBlockNodeState:
    output = common.adb_shell(
        adb,
        serial,
        PREPARE_SCRIPT,
        EXACT_NODE,
        EXACT_SYSFS,
        EXACT_KERNEL_NAME,
        EXACT_BYTES,
    )
    return state_from_output(output)


def cleanup(common, adb: str, serial: str, state: ExactBlockNodeState) -> str:
    output = common.adb_shell(
        adb,
        serial,
        CLEANUP_SCRIPT,
        state.node,
        "yes" if state.created else "no",
        "yes" if state.parent_created else "no",
        state.kernel_dev,
    )
    if output.count("exact_block_node_cleanup_status=passed") != 1:
        raise ExactBlockNodeError("exact block-node cleanup did not report success")
    return output


@contextmanager
def exact_block_node(common, adb: str, serial: str) -> Iterator[ExactBlockNodeState]:
    state = prepare(common, adb, serial)
    try:
        yield state
    finally:
        cleanup(common, adb, serial, state)

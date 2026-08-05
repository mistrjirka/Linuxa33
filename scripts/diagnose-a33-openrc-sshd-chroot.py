#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / "flash-a33-u0i-python-direct-root-v2.py"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_USERDATA = "/dev/block/sda36"
EXPECTED_USERDATA_BYTES = "114240258048"
EXPECTED_UUID = "7b056328-bdfb-496b-ac38-2624c43c863a"
EXPECTED_LABEL = "pmOS_root"

spec = importlib.util.spec_from_file_location("a33_openrc_sshd_common", COMMON_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load A33 recovery helper: {COMMON_PATH}")
common = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = common
spec.loader.exec_module(common)


class DiagnosticError(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -u
target="$1"
expected="$2"
root=/tmp/a33x-openrc-sshd-root
work=/tmp/a33x-openrc-sshd-work
root_mounted=no
dev_mounted=no
proc_mounted=no
sys_mounted=no
run_mounted=no
cgroup_masked=no
service_started=no

snapshot()
{
    label="$1"
    echo "snapshot_begin=$label"
    pidfile="$root/run/sshd.pid"
    if [ -s "$pidfile" ]; then
        pid="$(cat "$pidfile" 2>/dev/null || true)"
        echo "pidfile_present=yes"
        echo "pid=${pid:-missing}"
    else
        pid=""
        echo "pidfile_present=no"
        echo "pid=missing"
    fi

    alive=no
    cmdline=""
    wchan=""
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        alive=yes
        cmdline="$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        wchan="$(cat "/proc/$pid/wchan" 2>/dev/null || true)"
    fi
    echo "process_alive=$alive"
    echo "process_cmdline=$cmdline"
    echo "process_wchan=$wchan"

    listener=no
    if awk '$2 ~ /:0016$/ && $4 == "0A" { found=1 } END { exit found ? 0 : 1 }' \
        /proc/net/tcp /proc/net/tcp6 2>/dev/null; then
        listener=yes
    fi
    echo "port22_listener=$listener"

    echo "openrc_state_begin"
    find "$root/run/openrc" -maxdepth 3 \( -type f -o -type l \) -print 2>/dev/null |
        sort |
        while read -r file; do
            relative="${file#$root}"
            target_value=""
            [ ! -L "$file" ] || target_value="$(readlink "$file" 2>/dev/null || true)"
            contents=""
            [ ! -f "$file" ] || contents="$(cat "$file" 2>/dev/null | tr '\n' ' ' || true)"
            echo "openrc_path=$relative symlink_target=$target_value contents=$contents"
        done
    echo "openrc_state_end"

    echo "process_matches_begin"
    ps -ef 2>/dev/null | grep '[s]shd' || true
    echo "process_matches_end"
    echo "snapshot_end=$label"
}

stop_service()
{
    if [ "$root_mounted" = yes ] && [ "$run_mounted" = yes ]; then
        chroot "$root" /etc/init.d/sshd stop >/dev/null 2>&1 || true
    fi
    pid="$(cat "$root/run/sshd.pid" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
    fi
    service_started=no
}

cleanup()
{
    stop_service
    if [ "$cgroup_masked" = yes ]; then
        umount "$root/usr/libexec/rc/sh/rc-cgroup.sh" 2>/dev/null || true
        cgroup_masked=no
    fi
    [ "$run_mounted" = no ] || umount "$root/run" 2>/dev/null || true
    run_mounted=no
    [ "$sys_mounted" = no ] || umount "$root/sys" 2>/dev/null || true
    sys_mounted=no
    [ "$proc_mounted" = no ] || umount "$root/proc" 2>/dev/null || true
    proc_mounted=no
    [ "$dev_mounted" = no ] || umount "$root/dev" 2>/dev/null || true
    dev_mounted=no
    [ "$root_mounted" = no ] || umount "$root" 2>/dev/null || true
    root_mounted=no
    rm -rf "$root" "$work" 2>/dev/null || true
}
trap cleanup EXIT

resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target=$target"
echo "target_resolved=$resolved"
[ "$resolved" = "$expected" ] || exit 20

for command in mount umount chroot find sort cat tr readlink kill sleep awk ps grep stat sha256sum mkdir rm; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_twrp_command=$command"
        exit 21
    }
done

rm -rf "$root" "$work"
mkdir -p "$root" "$work"
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
echo "readonly_root_mount=passed"

for required in \
    /sbin/openrc-run \
    /etc/init.d/sshd \
    /usr/libexec/rc/sh/rc-cgroup.sh \
    /usr/sbin/sshd.pam \
    /etc/ssh/sshd_config; do
    [ -e "$root$required" ] || {
        echo "missing_rootfs_path=$required"
        exit 22
    }
done

mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=8m tmpfs "$root/run"
run_mounted=yes
mkdir -p \
    "$root/run/openrc" \
    "$root/run/openrc/started" \
    "$root/run/openrc/starting" \
    "$root/run/openrc/stopping" \
    "$root/run/openrc/inactive" \
    "$root/run/openrc/failed" \
    "$root/run/openrc/crashed" \
    "$root/run/openrc/daemons" \
    "$root/run/lock"
printf '%s\n' default > "$root/run/openrc/softlevel"

mount -o bind /dev/null "$root/usr/libexec/rc/sh/rc-cgroup.sh"
cgroup_masked=yes
echo "openrc_cgroup_mask=passed"
echo "volatile_runtime_mounts=passed"
echo "userdata_persistent_writes=no"

start_output="$work/start.txt"
: > "$start_output"
chroot "$root" /etc/init.d/sshd start > "$start_output" 2>&1 &
launcher_pid=$!
start_elapsed=0
while kill -0 "$launcher_pid" 2>/dev/null && [ "$start_elapsed" -lt 30 ]; do
    sleep 1
    start_elapsed=$((start_elapsed + 1))
done
start_timed_out=no
if kill -0 "$launcher_pid" 2>/dev/null; then
    start_timed_out=yes
    kill -TERM "$launcher_pid" 2>/dev/null || true
    sleep 1
    kill -KILL "$launcher_pid" 2>/dev/null || true
fi
wait "$launcher_pid" 2>/dev/null
start_rc=$?
[ "$start_timed_out" = no ] || start_rc=124

echo "openrc_start_rc=$start_rc"
echo "openrc_start_timed_out=$start_timed_out"
echo "openrc_start_elapsed_seconds=$start_elapsed"
echo "openrc_start_output_begin"
cat "$start_output" 2>&1 || true
echo "openrc_start_output_end"

[ "$start_rc" -ne 0 ] || service_started=yes
snapshot after-start
for second in 1 2 3 4 5; do
    sleep 1
    snapshot "second-$second"
done

status_output="$work/status.txt"
chroot "$root" /etc/init.d/sshd status > "$status_output" 2>&1
status_rc=$?
echo "openrc_status_rc=$status_rc"
echo "openrc_status_output_begin"
cat "$status_output" 2>&1 || true
echo "openrc_status_output_end"

stop_output="$work/stop.txt"
chroot "$root" /etc/init.d/sshd stop > "$stop_output" 2>&1
stop_rc=$?
service_started=no
echo "openrc_stop_rc=$stop_rc"
echo "openrc_stop_output_begin"
cat "$stop_output" 2>&1 || true
echo "openrc_stop_output_end"
snapshot after-stop

echo "userdata_persistent_writes=no"
echo "phone_partition_writes=no"
echo "phone_reboot_performed=no"

umount "$root/usr/libexec/rc/sh/rc-cgroup.sh"
cgroup_masked=no
umount "$root/run"
run_mounted=no
umount "$root/sys"
sys_mounted=no
umount "$root/proc"
proc_mounted=no
umount "$root/dev"
dev_mounted=no
umount "$root"
root_mounted=no
echo "cleanup_unmount=passed"
'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else "missing"


def section(text: str, begin: str, end: str) -> str:
    start = f"{begin}\n"
    finish = f"{end}\n"
    if text.count(start) != 1 or text.count(finish) != 1:
        return ""
    return text.split(start, 1)[1].split(finish, 1)[0]


def parse_snapshots(text: str) -> list[dict[str, object]]:
    snapshots: list[dict[str, object]] = []
    pattern = re.compile(
        r"^snapshot_begin=(.*?)\n(.*?)^snapshot_end=\1$",
        re.MULTILINE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        body = match.group(2)
        snapshots.append(
            {
                "label": match.group(1),
                "pidfile_present": value(body, "pidfile_present"),
                "pid": value(body, "pid"),
                "process_alive": value(body, "process_alive"),
                "process_cmdline": value(body, "process_cmdline"),
                "process_wchan": value(body, "process_wchan"),
                "port22_listener": value(body, "port22_listener"),
                "openrc_state": section(
                    body, "openrc_state_begin", "openrc_state_end"
                ).splitlines(),
                "process_matches": section(
                    body, "process_matches_begin", "process_matches_end"
                ).splitlines(),
            }
        )
    return snapshots


def diagnose(start_rc: str, start_timed_out: str, snapshots: list[dict[str, object]]) -> str:
    if start_timed_out == "yes":
        return "exact-openrc-sshd-start-timed-out"
    if start_rc != "0":
        return "exact-openrc-sshd-start-failed"
    active = [
        item
        for item in snapshots
        if item["label"] != "after-stop"
        and item["process_alive"] == "yes"
        and item["port22_listener"] == "yes"
    ]
    if active:
        return "exact-openrc-sshd-path-works-real-boot-later-stop-or-ordering"
    if any(item["pidfile_present"] == "yes" for item in snapshots):
        return "openrc-start-returned-but-sshd-exited"
    return "openrc-start-returned-without-pid-or-listener"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run the exact installed OpenRC sshd service path in a read-only TWRP "
            "chroot with the U0l cgroup helper mask"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    serial = common.select_recovery(adb, 30)

    values, sections = common.live_state(adb, serial)
    expected = {
        "recovery_sha": EXPECTED_TWRP_SHA256,
        "userdata_resolved": EXPECTED_USERDATA,
        "userdata_bytes": EXPECTED_USERDATA_BYTES,
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for name in ("mount_users", "swap_users", "dm_users"):
        if sections.get(name):
            mismatches.append(f"{name}: active={sections[name]!r}")
    uuid_value, label = common.ext4_identity(adb, serial)
    if uuid_value != EXPECTED_UUID:
        mismatches.append(
            f"filesystem_uuid: actual={uuid_value!r} expected={EXPECTED_UUID!r}"
        )
    if label != EXPECTED_LABEL:
        mismatches.append(
            f"filesystem_label: actual={label!r} expected={EXPECTED_LABEL!r}"
        )
    if mismatches:
        raise DiagnosticError("unsafe TWRP/userdata state:\n" + "\n".join(mismatches))

    completed = common.run(
        [
            adb,
            "-s",
            serial,
            "shell",
            "sh",
            "-s",
            "--",
            common.USERDATA,
            EXPECTED_USERDATA,
        ],
        input_data=REMOTE_SCRIPT,
        check=False,
        timeout=150,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise DiagnosticError(
            f"exact OpenRC SSH diagnostic failed rc={completed.returncode}:\n"
            f"{output}\n{stderr}"
        )
    for marker in (
        "readonly_root_mount=passed",
        "openrc_cgroup_mask=passed",
        "volatile_runtime_mounts=passed",
        "userdata_persistent_writes=no",
        "phone_partition_writes=no",
        "phone_reboot_performed=no",
        "cleanup_unmount=passed",
    ):
        if marker not in output:
            raise DiagnosticError(f"missing safety marker: {marker}")

    snapshots = parse_snapshots(output)
    if len(snapshots) != 7:
        raise DiagnosticError(f"unexpected snapshot count: {len(snapshots)}")
    start_rc = value(output, "openrc_start_rc")
    start_timed_out = value(output, "openrc_start_timed_out")
    diagnosis = diagnose(start_rc, start_timed_out, snapshots)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-openrc-sshd-chroot-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "diagnostic.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    summary_values = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "diagnose-a33-openrc-sshd-chroot",
        "implementation_language": "python3",
        "adb_serial": serial,
        "twrp_recovery_sha256": values["recovery_sha"],
        "userdata_resolved": values["userdata_resolved"],
        "userdata_filesystem_uuid": uuid_value,
        "userdata_filesystem_label": label,
        "readonly_root_mount_passed": True,
        "openrc_cgroup_mask_applied": True,
        "openrc_start_rc": start_rc,
        "openrc_start_timed_out": start_timed_out,
        "openrc_start_output": section(
            output, "openrc_start_output_begin", "openrc_start_output_end"
        ).splitlines(),
        "openrc_status_rc": value(output, "openrc_status_rc"),
        "openrc_status_output": section(
            output, "openrc_status_output_begin", "openrc_status_output_end"
        ).splitlines(),
        "openrc_stop_rc": value(output, "openrc_stop_rc"),
        "snapshots": snapshots,
        "diagnosis": diagnosis,
        "raw_report": str(raw),
        "raw_report_sha256": sha256_file(raw),
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "diagnostic_status": "passed",
    }
    summary = out / "summary.json"
    summary.write_text(
        json.dumps(summary_values, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(out, arcname=out.name)

    print(json.dumps(summary_values, indent=2, sort_keys=True))
    print(f"diagnostic_directory={out}")
    print(f"diagnostic_archive={archive}")
    print(f"diagnostic_archive_sha256={sha256_file(archive)}")
    print("userdata_persistent_writes=no")
    print("phone_partition_writes=no")
    print("phone_reboot_performed=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DiagnosticError, common.Refusal, OSError, ValueError) as exc:
        print(f"OPENRC SSH CHROOT DIAGNOSTIC FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / "flash-a33-u0i-python-direct-root-v2.py"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
CHROOT_ROOT = "/tmp/a33x-openrc-sshd-root"
CHROOT_WORK = "/tmp/a33x-openrc-sshd-work"

spec = importlib.util.spec_from_file_location("a33_openrc_sshd_cleanup_common", COMMON_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load A33 recovery helper: {COMMON_PATH}")
common = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = common
spec.loader.exec_module(common)


class CleanupError(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -u
root="$1"
work="$2"

echo "cleanup_root=$root"
echo "cleanup_work=$work"

echo "mounts_before_begin"
awk -v root="$root" '$2 == root || index($2, root "/") == 1 { print }' /proc/mounts 2>/dev/null || true
echo "mounts_before_end"

echo "chroot_processes_before_begin"
for proc in /proc/[0-9]*; do
    [ -d "$proc" ] || continue
    pid="${proc##*/}"
    proc_root="$(readlink "$proc/root" 2>/dev/null || true)"
    case "$proc_root" in
        "$root"|"$root/"*)
            cmdline="$(tr '\000' ' ' < "$proc/cmdline" 2>/dev/null || true)"
            echo "chroot_process pid=$pid root=$proc_root cmdline=$cmdline"
            ;;
    esac
done
echo "chroot_processes_before_end"

term_count=0
for proc in /proc/[0-9]*; do
    [ -d "$proc" ] || continue
    pid="${proc##*/}"
    proc_root="$(readlink "$proc/root" 2>/dev/null || true)"
    case "$proc_root" in
        "$root"|"$root/"*)
            cmdline="$(tr '\000' ' ' < "$proc/cmdline" 2>/dev/null || true)"
            echo "sending_term pid=$pid cmdline=$cmdline"
            kill -TERM "$pid" 2>/dev/null || true
            term_count=$((term_count + 1))
            ;;
    esac
done
echo "term_count=$term_count"
sleep 2

kill_count=0
for proc in /proc/[0-9]*; do
    [ -d "$proc" ] || continue
    pid="${proc##*/}"
    proc_root="$(readlink "$proc/root" 2>/dev/null || true)"
    case "$proc_root" in
        "$root"|"$root/"*)
            cmdline="$(tr '\000' ' ' < "$proc/cmdline" 2>/dev/null || true)"
            echo "sending_kill pid=$pid cmdline=$cmdline"
            kill -KILL "$pid" 2>/dev/null || true
            kill_count=$((kill_count + 1))
            ;;
    esac
done
echo "kill_count=$kill_count"
sleep 1
sync 2>/dev/null || true

is_mounted()
{
    point="$1"
    awk -v point="$point" '$2 == point { found=1 } END { exit found ? 0 : 1 }' /proc/mounts
}

unmount_one()
{
    point="$1"
    if is_mounted "$point"; then
        echo "unmount_attempt=$point"
        if umount "$point"; then
            echo "unmount_result=passed path=$point"
        else
            echo "unmount_result=failed path=$point"
            return 1
        fi
    else
        echo "unmount_result=not-mounted path=$point"
    fi
    return 0
}

cleanup_failed=no
for point in \
    "$root/usr/libexec/rc/sh/rc-cgroup.sh" \
    "$root/run" \
    "$root/sys" \
    "$root/proc" \
    "$root/dev"; do
    unmount_one "$point" || cleanup_failed=yes
done

root_mode_before=not-mounted
if is_mounted "$root"; then
    root_mode_before="$(awk -v point="$root" '$2 == point { print $4; exit }' /proc/mounts)"
    echo "root_mount_options_before=$root_mode_before"
    if mount -o remount,ro "$root" 2>/dev/null; then
        echo "root_remount_readonly=passed"
    else
        root_mode_now="$(awk -v point="$root" '$2 == point { print $4; exit }' /proc/mounts)"
        case ",$root_mode_now," in
            *,ro,*) echo "root_remount_readonly=already-readonly" ;;
            *)
                echo "root_remount_readonly=failed options=$root_mode_now"
                cleanup_failed=yes
                ;;
        esac
    fi
    sync 2>/dev/null || true
    unmount_one "$root" || cleanup_failed=yes
else
    echo "root_mount_options_before=not-mounted"
    echo "root_remount_readonly=not-mounted"
fi

echo "mounts_after_begin"
remaining_mounts="$(awk -v root="$root" '$2 == root || index($2, root "/") == 1 { print }' /proc/mounts 2>/dev/null || true)"
printf '%s\n' "$remaining_mounts"
echo "mounts_after_end"
[ -z "$remaining_mounts" ] || cleanup_failed=yes

echo "chroot_processes_after_begin"
remaining_processes=""
for proc in /proc/[0-9]*; do
    [ -d "$proc" ] || continue
    pid="${proc##*/}"
    proc_root="$(readlink "$proc/root" 2>/dev/null || true)"
    case "$proc_root" in
        "$root"|"$root/"*)
            cmdline="$(tr '\000' ' ' < "$proc/cmdline" 2>/dev/null || true)"
            line="chroot_process pid=$pid root=$proc_root cmdline=$cmdline"
            echo "$line"
            remaining_processes="${remaining_processes}${line}
"
            ;;
    esac
done
echo "chroot_processes_after_end"
[ -z "$remaining_processes" ] || cleanup_failed=yes

if [ "$cleanup_failed" = no ]; then
    rm -rf "$root" "$work" 2>/dev/null || true
    echo "cleanup_status=passed"
else
    echo "cleanup_status=failed"
    exit 30
fi

echo "possible_persistent_writes=yes-unsafe-diagnostic-remounted-root-rw"
echo "phone_partition_writes=cleanup-only-no-new-intended-writes"
echo "phone_reboot_performed=no"
'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def section(text: str, begin: str, end: str) -> list[str]:
    start = f"{begin}\n"
    finish = f"{end}\n"
    if text.count(start) != 1 or text.count(finish) != 1:
        return []
    return text.split(start, 1)[1].split(finish, 1)[0].splitlines()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clean up mounts and chrooted processes left by the disabled A33 OpenRC SSH diagnostic"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    serial = common.select_recovery(adb, 30)

    values, _ = common.live_state(adb, serial)
    if values.get("recovery_sha") != EXPECTED_TWRP_SHA256:
        raise CleanupError(
            "exact TWRP recovery mismatch: "
            f"actual={values.get('recovery_sha')!r} expected={EXPECTED_TWRP_SHA256!r}"
        )

    completed = common.run(
        [
            adb,
            "-s",
            serial,
            "shell",
            "sh",
            "-s",
            "--",
            CHROOT_ROOT,
            CHROOT_WORK,
        ],
        input_data=REMOTE_SCRIPT,
        check=False,
        timeout=90,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise CleanupError(
            f"OpenRC SSH chroot cleanup failed rc={completed.returncode}:\n"
            f"{output}\n{stderr}"
        )
    if "cleanup_status=passed" not in output:
        raise CleanupError("cleanup did not report success")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-openrc-sshd-chroot-cleanup-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "cleanup.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "cleanup-disabled-a33-openrc-sshd-chroot-diagnostic",
        "implementation_language": "python3",
        "adb_serial": serial,
        "twrp_recovery_sha256": values["recovery_sha"],
        "cleanup_status": "passed",
        "mounts_before": section(output, "mounts_before_begin", "mounts_before_end"),
        "mounts_after": section(output, "mounts_after_begin", "mounts_after_end"),
        "chroot_processes_before": section(
            output, "chroot_processes_before_begin", "chroot_processes_before_end"
        ),
        "chroot_processes_after": section(
            output, "chroot_processes_after_begin", "chroot_processes_after_end"
        ),
        "possible_persistent_writes": "yes-unsafe-diagnostic-remounted-root-rw",
        "phone_reboot_performed": "no",
        "raw_report": str(raw),
        "raw_report_sha256": sha256_file(raw),
    }
    summary_path = out / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)

    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"cleanup_directory={out}")
    print(f"cleanup_archive={archive}")
    print(f"cleanup_archive_sha256={sha256_file(archive)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CleanupError, common.Refusal, OSError, ValueError) as exc:
        print(f"A33 OPENRC SSH CHROOT CLEANUP FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

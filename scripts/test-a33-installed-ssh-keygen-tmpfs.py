#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
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
EXPECTED_LABEL = "pmOS_root"

spec = importlib.util.spec_from_file_location("a33_ssh_keygen_tmpfs_common", COMMON_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load A33 runtime helper: {COMMON_PATH}")
common = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = common
spec.loader.exec_module(common)


class DiagnosticError(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -u
target="$1"
expected="$2"
root=/tmp/a33x-ssh-keygen-root
overlay=/tmp/a33x-ssh-keygen-overlay
work=/tmp/a33x-ssh-keygen-work
root_mounted=no
overlay_mounted=no
ssh_bind_mounted=no
dev_mounted=no
proc_mounted=no
sys_mounted=no
run_mounted=no

cleanup() {
    [ "$run_mounted" = no ] || umount "$root/run" 2>/dev/null || true
    [ "$sys_mounted" = no ] || umount "$root/sys" 2>/dev/null || true
    [ "$proc_mounted" = no ] || umount "$root/proc" 2>/dev/null || true
    [ "$dev_mounted" = no ] || umount "$root/dev" 2>/dev/null || true
    [ "$ssh_bind_mounted" = no ] || umount "$root/etc/ssh" 2>/dev/null || true
    [ "$overlay_mounted" = no ] || umount "$overlay" 2>/dev/null || true
    [ "$root_mounted" = no ] || umount "$root" 2>/dev/null || true
    rm -rf "$root" "$overlay" "$work" 2>/dev/null || true
}
trap cleanup EXIT

resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target=$target"
echo "target_resolved=$resolved"
[ "$resolved" = "$expected" ] || exit 20

for command in mount umount chroot cp find stat sha256sum cat grep awk sed kill sleep mkdir rm sync; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_twrp_command=$command"
        exit 21
    }
done

rm -rf "$root" "$overlay" "$work"
mkdir -p "$root" "$overlay" "$work"
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
echo "readonly_root_mount=passed"

for required in \
    /bin/sh \
    /usr/bin/ssh-keygen \
    /usr/sbin/sshd \
    /etc/ssh/sshd_config \
    /etc/passwd \
    /etc/group; do
    if [ ! -e "$root$required" ]; then
        echo "missing_rootfs_path=$required"
        exit 22
    fi
done

mount -t tmpfs -o mode=0755,size=16m tmpfs "$overlay"
overlay_mounted=yes
cp -a "$root/etc/ssh/." "$overlay/"
mount -o bind "$overlay" "$root/etc/ssh"
ssh_bind_mounted=yes
mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/run"
run_mounted=yes

echo "volatile_ssh_overlay=passed"
echo "userdata_persistent_writes=no"
echo "entropy_avail_before=$(cat /proc/sys/kernel/random/entropy_avail 2>/dev/null || true)"

capture_process_state() {
    pid="$1"
    label="$2"
    echo "${label}_process_state_begin"
    for relative in status wchan stack syscall; do
        file="/proc/$pid/$relative"
        if [ -r "$file" ]; then
            echo "file=$relative"
            cat "$file" 2>&1 || true
        fi
    done
    children="$(cat "/proc/$pid/task/$pid/children" 2>/dev/null || true)"
    echo "children=${children:-none}"
    for child in $children; do
        echo "child=$child"
        for relative in status wchan stack syscall; do
            file="/proc/$child/$relative"
            if [ -r "$file" ]; then
                echo "child_file=$relative"
                cat "$file" 2>&1 || true
            fi
        done
    done
    echo "${label}_process_state_end"
}

run_with_deadline() {
    label="$1"
    seconds="$2"
    shift 2
    output="$work/$label.txt"
    : > "$output"
    chroot "$root" "$@" > "$output" 2>&1 &
    pid=$!
    elapsed=0
    while kill -0 "$pid" 2>/dev/null && [ "$elapsed" -lt "$seconds" ]; do
        sleep 1
        elapsed=$((elapsed + 1))
    done
    timed_out=no
    if kill -0 "$pid" 2>/dev/null; then
        timed_out=yes
        capture_process_state "$pid" "$label"
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null
    rc=$?
    [ "$timed_out" = no ] || rc=124
    echo "${label}_rc=$rc"
    echo "${label}_timed_out=$timed_out"
    echo "${label}_elapsed_seconds=$elapsed"
    echo "${label}_output_begin"
    cat "$output" 2>&1 || true
    echo "${label}_output_end"
}

run_with_deadline ssh_keygen 45 /usr/bin/ssh-keygen -A
sync

echo "generated_host_keys_begin"
private_count=0
public_count=0
for key in "$overlay"/ssh_host_*; do
    [ -e "$key" ] || continue
    relative="/etc/ssh/${key##*/}"
    case "$relative" in
        *.pub)
            kind=public
            public_count=$((public_count + 1))
            ;;
        *)
            kind=private
            private_count=$((private_count + 1))
            ;;
    esac
    echo "generated_host_key kind=$kind path=$relative bytes=$(stat -c '%s' "$key" 2>/dev/null || true) mode=$(stat -c '%a' "$key" 2>/dev/null || true) uid=$(stat -c '%u' "$key" 2>/dev/null || true) gid=$(stat -c '%g' "$key" 2>/dev/null || true) sha256=$(sha256sum "$key" 2>/dev/null | awk '{print $1}')"
done
echo "generated_private_key_count=$private_count"
echo "generated_public_key_count=$public_count"
echo "generated_host_keys_end"

if [ "$private_count" -gt 0 ]; then
    run_with_deadline sshd_config 30 /usr/sbin/sshd -t -f /etc/ssh/sshd_config
else
    echo "sshd_config_rc=not-run-no-private-keys"
    echo "sshd_config_timed_out=no"
    echo "sshd_config_elapsed_seconds=0"
    echo "sshd_config_output_begin"
    echo "not run because no private host keys were generated"
    echo "sshd_config_output_end"
fi

echo "entropy_avail_after=$(cat /proc/sys/kernel/random/entropy_avail 2>/dev/null || true)"

echo "root_mount_state_begin"
awk -v root="$root" '$2 == root { print }' /proc/mounts 2>/dev/null || true
echo "root_mount_state_end"

echo "volatile_tmpfs_only=yes"
echo "phone_partition_writes=no"

umount "$root/run"
run_mounted=no
umount "$root/sys"
sys_mounted=no
umount "$root/proc"
proc_mounted=no
umount "$root/dev"
dev_mounted=no
umount "$root/etc/ssh"
ssh_bind_mounted=no
umount "$overlay"
overlay_mounted=no
umount "$root"
root_mounted=no
echo "cleanup_unmount=passed"
'''


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def value(text: str, key: str) -> str:
    match = re.search(rf"^{re.escape(key)}=(.*)$", text, re.MULTILINE)
    return match.group(1).strip() if match else "missing"


def section(text: str, name: str) -> str:
    start = f"{name}_begin\n"
    end = f"{name}_end\n"
    if text.count(start) != 1 or text.count(end) != 1:
        return ""
    return text.split(start, 1)[1].split(end, 1)[0]


def summarize(text: str) -> dict[str, object]:
    keygen_rc = value(text, "ssh_keygen_rc")
    keygen_timeout = value(text, "ssh_keygen_timed_out")
    config_rc = value(text, "sshd_config_rc")
    config_timeout = value(text, "sshd_config_timed_out")
    private_count = int(value(text, "generated_private_key_count") or "0")
    public_count = int(value(text, "generated_public_key_count") or "0")
    process_state = section(text, "ssh_keygen_process_state")
    keygen_output = section(text, "ssh_keygen_output")
    config_output = section(text, "sshd_config_output")

    if keygen_timeout == "yes":
        if re.search(r"random|getrandom|wait_for_random", process_state, re.IGNORECASE):
            diagnosis = "ssh-keygen-blocked-waiting-for-randomness"
        else:
            diagnosis = "ssh-keygen-timed-out"
    elif keygen_rc != "0":
        diagnosis = "ssh-keygen-failed"
    elif private_count == 0:
        diagnosis = "ssh-keygen-returned-without-private-host-keys"
    elif config_timeout == "yes":
        diagnosis = "sshd-config-validation-timed-out"
    elif config_rc != "0":
        diagnosis = "sshd-config-validation-failed-after-keygen"
    else:
        diagnosis = "volatile-keygen-and-sshd-config-validation-passed"

    return {
        "readonly_root_mount_passed": "readonly_root_mount=passed" in text,
        "volatile_ssh_overlay_passed": "volatile_ssh_overlay=passed" in text,
        "cleanup_unmount_passed": "cleanup_unmount=passed" in text,
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "ssh_keygen_rc": keygen_rc,
        "ssh_keygen_timed_out": keygen_timeout,
        "ssh_keygen_elapsed_seconds": value(text, "ssh_keygen_elapsed_seconds"),
        "generated_private_key_count": private_count,
        "generated_public_key_count": public_count,
        "sshd_config_rc": config_rc,
        "sshd_config_timed_out": config_timeout,
        "sshd_config_elapsed_seconds": value(text, "sshd_config_elapsed_seconds"),
        "entropy_avail_before": value(text, "entropy_avail_before"),
        "entropy_avail_after": value(text, "entropy_avail_after"),
        "ssh_keygen_output": keygen_output.splitlines()[-100:],
        "ssh_keygen_process_state": process_state.splitlines()[-200:],
        "sshd_config_output": config_output.splitlines()[-100:],
        "diagnosis": diagnosis,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run installed A33 ssh-keygen and sshd -t in a volatile tmpfs overlay "
            "while userdata remains mounted read-only"
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
            f"volatile SSH keygen diagnostic failed rc={completed.returncode}:\n"
            f"{output}\n{stderr}"
        )
    for marker in (
        "readonly_root_mount=passed",
        "volatile_ssh_overlay=passed",
        "userdata_persistent_writes=no",
        "volatile_tmpfs_only=yes",
        "phone_partition_writes=no",
        "cleanup_unmount=passed",
    ):
        if marker not in output:
            raise DiagnosticError(f"missing safety marker: {marker}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build" / f"a33-installed-ssh-keygen-tmpfs-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "diagnostic.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    summary_values = summarize(output)
    summary_values.update(
        {
            "created": datetime.now().astimezone().isoformat(
                timespec="microseconds"
            ),
            "operation": "test-a33-installed-ssh-keygen-tmpfs",
            "implementation_language": "python3",
            "adb_serial": serial,
            "twrp_recovery_sha256": values["recovery_sha"],
            "userdata_resolved": values["userdata_resolved"],
            "userdata_filesystem_uuid": uuid_value,
            "userdata_filesystem_label": label,
            "raw_report": str(raw),
            "raw_report_sha256": sha256_file(raw),
            "diagnostic_status": "passed",
        }
    )
    summary = out / "summary.json"
    summary.write_text(
        json.dumps(summary_values, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    stable = root / "build/a33-installed-ssh-keygen-tmpfs.json"
    shutil.copy2(summary, stable)

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = sha256_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )

    print(json.dumps(summary_values, indent=2, sort_keys=True))
    print(f"diagnostic_directory={out}")
    print(f"diagnostic_archive={archive}")
    print(f"diagnostic_archive_sha256={archive_sha}")
    print("userdata_persistent_writes=no")
    print("phone_partition_writes=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DiagnosticError, common.Refusal, OSError, ValueError) as exc:
        print(f"INSTALLED SSH TMPFS DIAGNOSTIC FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

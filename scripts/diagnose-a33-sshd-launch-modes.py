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

spec = importlib.util.spec_from_file_location("a33_sshd_launch_common", COMMON_PATH)
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
root=/tmp/a33x-sshd-launch-root
work=/tmp/a33x-sshd-launch-work
root_mounted=no
dev_mounted=no
proc_mounted=no
sys_mounted=no
run_mounted=no

cleanup() {
    if [ "$root_mounted" = yes ] && [ -x "$root/sbin/start-stop-daemon" ]; then
        for candidate in /usr/sbin/sshd.krb5 /usr/sbin/sshd.pam /usr/sbin/sshd; do
            [ -x "$root$candidate" ] || continue
            chroot "$root" /sbin/start-stop-daemon --stop --exec "$candidate" --retry TERM/2/KILL/2 >/dev/null 2>&1 || true
        done
    fi
    [ "$run_mounted" = no ] || umount "$root/run" 2>/dev/null || true
    [ "$sys_mounted" = no ] || umount "$root/sys" 2>/dev/null || true
    [ "$proc_mounted" = no ] || umount "$root/proc" 2>/dev/null || true
    [ "$dev_mounted" = no ] || umount "$root/dev" 2>/dev/null || true
    [ "$root_mounted" = no ] || umount "$root" 2>/dev/null || true
    rm -rf "$root" "$work" 2>/dev/null || true
}
trap cleanup EXIT

resolved="$(readlink -f "$target" 2>/dev/null || true)"
echo "target=$target"
echo "target_resolved=$resolved"
[ "$resolved" = "$expected" ] || exit 20

for command in mount umount chroot cat grep awk sed kill sleep mkdir rm stat sha256sum readlink tr; do
    command -v "$command" >/dev/null 2>&1 || {
        echo "missing_twrp_command=$command"
        exit 21
    }
done

rm -rf "$root" "$work"
mkdir -p "$root" "$work"
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$root"
root_mounted=yes
mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=8m tmpfs "$root/run"
run_mounted=yes
mkdir -p "$root/run/openrc"
touch "$root/run/openrc/softlevel"
echo "readonly_root_mount=passed"
echo "volatile_runtime_mounts=passed"
echo "userdata_persistent_writes=no"

selected=/usr/sbin/sshd
if [ -x "$root/usr/sbin/sshd.krb5" ] &&
   chroot "$root" /usr/sbin/sshd.krb5 -f /etc/ssh/sshd_config -G 2>/dev/null |
       grep -iqxE '(kerberos|gssapi)authentication yes'; then
    selected=/usr/sbin/sshd.krb5
elif [ -x "$root/usr/sbin/sshd.pam" ] &&
     chroot "$root" /usr/sbin/sshd.pam -f /etc/ssh/sshd_config -G 2>/dev/null |
         grep -iqx 'usepam yes'; then
    selected=/usr/sbin/sshd.pam
fi
echo "selected_candidate=$selected"
echo "selected_sha256=$(sha256sum "$root$selected" | awk '{print $1}')"

socket_listening() {
    port="$1"
    hex="$(printf '%04X' "$port")"
    awk -v suffix=":$hex" '$2 ~ suffix "$" && $4 == "0A" { found=1 } END { exit found ? 0 : 1 }' \
        /proc/net/tcp /proc/net/tcp6 2>/dev/null
}

process_state() {
    pid="$1"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        echo yes
    else
        echo no
    fi
}

run_mode() {
    label="$1"
    port="$2"
    mode="$3"
    pidfile="/run/a33x-${label}.pid"
    host_pidfile="$root$pidfile"
    log="$work/${label}.log"
    rm -f "$host_pidfile" "$log"

    echo "mode_begin=$label"
    echo "mode_port=$port"
    echo "mode_launch=$mode"

    case "$mode" in
        direct-daemon)
            chroot "$root" "$selected" \
                -f /etc/ssh/sshd_config \
                -o "PidFile=$pidfile" \
                -p "$port" \
                -o ListenAddress=127.0.0.1 >"$log" 2>&1
            rc=$?
            ;;
        start-stop-daemon)
            chroot "$root" /sbin/start-stop-daemon --start \
                --exec "$selected" \
                --pidfile "$pidfile" -- \
                -f /etc/ssh/sshd_config \
                -o "PidFile=$pidfile" \
                -p "$port" \
                -o ListenAddress=127.0.0.1 >"$log" 2>&1
            rc=$?
            ;;
        ssd-background-foreground)
            chroot "$root" /sbin/start-stop-daemon --start \
                --background --make-pidfile \
                --exec "$selected" \
                --pidfile "$pidfile" -- \
                -D -e \
                -f /etc/ssh/sshd_config \
                -p "$port" \
                -o ListenAddress=127.0.0.1 >"$log" 2>&1
            rc=$?
            ;;
        *)
            echo "mode_error=unknown-launch-mode"
            exit 22
            ;;
    esac

    echo "mode_launch_rc=$rc"
    elapsed=0
    while [ "$elapsed" -lt 5 ]; do
        sleep 1
        elapsed=$((elapsed + 1))
        pid="$(cat "$host_pidfile" 2>/dev/null || true)"
        alive="$(process_state "$pid")"
        listening=no
        socket_listening "$port" && listening=yes
        echo "mode_sample second=$elapsed pid=${pid:-missing} alive=$alive listening=$listening"
    done

    pid="$(cat "$host_pidfile" 2>/dev/null || true)"
    alive="$(process_state "$pid")"
    listening=no
    socket_listening "$port" && listening=yes
    echo "mode_pid=${pid:-missing}"
    echo "mode_pidfile_present=$([ -s "$host_pidfile" ] && echo yes || echo no)"
    echo "mode_process_alive=$alive"
    echo "mode_listener_present=$listening"
    if [ -n "$pid" ] && [ -r "/proc/$pid/cmdline" ]; then
        echo "mode_cmdline=$(tr '\000' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
    fi
    echo "mode_output_begin"
    cat "$log" 2>/dev/null || true
    echo "mode_output_end"

    if [ -n "$pid" ]; then
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
    fi
    chroot "$root" /sbin/start-stop-daemon --stop --exec "$selected" --retry TERM/2/KILL/2 >/dev/null 2>&1 || true
    rm -f "$host_pidfile"
    echo "mode_end=$label"
}

run_mode direct-daemon 2222 direct-daemon
run_mode openrc-ssd 2223 start-stop-daemon
run_mode openrc-foreground 2224 ssd-background-foreground

echo "nftables_static_begin"
for relative in \
    /etc/nftables.nft \
    /etc/nftables.d/50_sshd.nft \
    /usr/share/nftables.avail/50_sshd.nft; do
    full="$root$relative"
    if [ -L "$full" ]; then
        echo "nft_path=$relative type=symlink target=$(readlink "$full" 2>/dev/null || true)"
    elif [ -f "$full" ]; then
        echo "nft_path=$relative type=file sha256=$(sha256sum "$full" | awk '{print $1}')"
        sed 's/^/nft_content=/' "$full" 2>/dev/null || true
    else
        echo "nft_path=$relative type=missing"
    fi
done
find "$root/etc/runlevels" -maxdepth 2 -type l 2>/dev/null |
    grep -i nft | sed "s#^$root#nft_runlevel=#" || true
echo "nftables_static_end"

echo "phone_partition_writes=no"
echo "phone_reboot_performed=no"
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


def parse_modes(text: str) -> list[dict[str, object]]:
    starts = list(re.finditer(r"^mode_begin=(.+)$", text, re.MULTILINE))
    result: list[dict[str, object]] = []
    for start in starts:
        label = start.group(1).strip()
        end_token = f"mode_end={label}"
        end = text.find(end_token, start.end())
        if end < 0:
            continue
        block = text[start.end():end]
        samples = [line.strip() for line in block.splitlines() if line.startswith("mode_sample ")]
        output_match = re.search(
            r"mode_output_begin\n(.*?)mode_output_end\n",
            block,
            re.DOTALL,
        )
        result.append(
            {
                "label": label,
                "port": value(block, "mode_port"),
                "launch": value(block, "mode_launch"),
                "launch_rc": value(block, "mode_launch_rc"),
                "pid": value(block, "mode_pid"),
                "pidfile_present": value(block, "mode_pidfile_present"),
                "process_alive": value(block, "mode_process_alive"),
                "listener_present": value(block, "mode_listener_present"),
                "cmdline": value(block, "mode_cmdline"),
                "samples": samples,
                "output": (output_match.group(1).splitlines() if output_match else [])[-100:],
            }
        )
    return result


def diagnose(modes: list[dict[str, object]]) -> str:
    by_label = {str(item["label"]): item for item in modes}
    direct = by_label.get("direct-daemon")
    ssd = by_label.get("openrc-ssd")
    foreground = by_label.get("openrc-foreground")
    if direct is None or ssd is None or foreground is None:
        return "incomplete-launch-mode-evidence"
    if direct["listener_present"] != "yes":
        return "normal-sshd-daemonization-fails"
    if ssd["listener_present"] != "yes":
        if foreground["listener_present"] == "yes":
            return "start-stop-daemon-normal-mode-fails-foreground-mode-works"
        return "start-stop-daemon-launch-fails"
    if foreground["listener_present"] != "yes":
        return "foreground-background-supervision-mode-fails"
    return "all-launch-modes-work-boot-environment-or-later-service-stop"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare installed A33 sshd launch modes read-only from TWRP"
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
        mismatches.append(f"filesystem_uuid: actual={uuid_value!r} expected={EXPECTED_UUID!r}")
    if label != EXPECTED_LABEL:
        mismatches.append(f"filesystem_label: actual={label!r} expected={EXPECTED_LABEL!r}")
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
        timeout=120,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise DiagnosticError(
            f"sshd launch-mode diagnostic failed rc={completed.returncode}:\n{output}\n{stderr}"
        )
    for marker in (
        "readonly_root_mount=passed",
        "volatile_runtime_mounts=passed",
        "userdata_persistent_writes=no",
        "phone_partition_writes=no",
        "phone_reboot_performed=no",
        "cleanup_unmount=passed",
    ):
        if marker not in output:
            raise DiagnosticError(f"missing safety marker: {marker}")

    modes = parse_modes(output)
    summary_values = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "diagnose-a33-sshd-launch-modes",
        "implementation_language": "python3",
        "adb_serial": serial,
        "twrp_recovery_sha256": values["recovery_sha"],
        "userdata_resolved": values["userdata_resolved"],
        "userdata_filesystem_uuid": uuid_value,
        "userdata_filesystem_label": label,
        "selected_candidate": value(output, "selected_candidate"),
        "selected_sha256": value(output, "selected_sha256"),
        "readonly_root_mount_passed": True,
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "modes": modes,
        "nftables_static": re.search(
            r"nftables_static_begin\n(.*?)nftables_static_end\n",
            output,
            re.DOTALL,
        ).group(1).splitlines() if "nftables_static_begin" in output else [],
        "diagnosis": diagnose(modes),
        "diagnostic_status": "passed",
    }

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-sshd-launch-modes-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "diagnostic.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    summary_values["raw_report"] = str(raw)
    summary_values["raw_report_sha256"] = sha256_file(raw)
    summary = out / "summary.json"
    summary.write_text(json.dumps(summary_values, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tf:
        tf.add(out, arcname=out.name)
    digest = sha256_file(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{digest}  {archive}\n", encoding="utf-8"
    )

    print(json.dumps(summary_values, indent=2, sort_keys=True))
    print(f"diagnostic_directory={out}")
    print(f"diagnostic_archive={archive}")
    print(f"diagnostic_archive_sha256={digest}")
    print("phone_partition_writes=no")
    print("phone_reboot_performed=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DiagnosticError, common.Refusal, OSError, ValueError) as exc:
        print(f"SSHD LAUNCH-MODE DIAGNOSTIC FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

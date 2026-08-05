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

spec = importlib.util.spec_from_file_location("a33_sshd_variant_common", COMMON_PATH)
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
root=/tmp/a33x-sshd-variant-root
work=/tmp/a33x-sshd-variant-work
root_mounted=no
dev_mounted=no
proc_mounted=no
sys_mounted=no
run_mounted=no
active_pid=""

cleanup() {
    if [ -n "$active_pid" ] && kill -0 "$active_pid" 2>/dev/null; then
        kill -TERM "$active_pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$active_pid" 2>/dev/null || true
        wait "$active_pid" 2>/dev/null || true
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

for command in mount umount chroot find stat sha256sum cat grep awk sed kill sleep mkdir rm sync readlink; do
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

rootfs_required_path() {
    relative="$1"
    full="$root$relative"
    if [ -e "$full" ]; then
        return 0
    fi
    [ -L "$full" ] || return 1
    link="$(readlink "$full" 2>/dev/null || true)"
    [ -n "$link" ] || return 1
    case "$link" in
        /*) candidate="$root$link" ;;
        *) candidate="${full%/*}/$link" ;;
    esac
    [ -e "$candidate" ] || [ -L "$candidate" ]
}

for required in /bin/sh /usr/sbin/sshd /etc/init.d/sshd /etc/ssh/sshd_config /etc/passwd /etc/group /var/empty; do
    if ! rootfs_required_path "$required"; then
        echo "missing_rootfs_path=$required"
        exit 22
    fi
done

mount -o bind /dev "$root/dev"
dev_mounted=yes
mount -t proc proc "$root/proc"
proc_mounted=yes
mount -t sysfs sysfs "$root/sys"
sys_mounted=yes
mount -t tmpfs -o mode=0755,size=4m tmpfs "$root/run"
run_mounted=yes
mkdir -p "$root/run/sshd"
chmod 755 "$root/run/sshd"
echo "volatile_runtime_mounts=passed"
echo "userdata_persistent_writes=no"

is_yes() {
    case "$1" in
        [Yy]|[Yy][Ee][Ss]|[Tt][Rr][Uu][Ee]|1) return 0 ;;
        *) return 1 ;;
    esac
}

sshd_disable_krb5="${SSHD_DISABLE_KRB5:-no}"
sshd_disable_pam="${SSHD_DISABLE_PAM:-no}"
if [ -f "$root/etc/conf.d/sshd" ]; then
    . "$root/etc/conf.d/sshd"
fi
sshd_disable_krb5="${sshd_disable_krb5:-${SSHD_DISABLE_KRB5:-no}}"
sshd_disable_pam="${sshd_disable_pam:-${SSHD_DISABLE_PAM:-no}}"

selected=/usr/sbin/sshd
if [ -x "$root/usr/sbin/sshd.krb5" ] && ! is_yes "$sshd_disable_krb5"; then
    selected=/usr/sbin/sshd.krb5
elif [ -x "$root/usr/sbin/sshd.pam" ] && ! is_yes "$sshd_disable_pam"; then
    selected=/usr/sbin/sshd.pam
fi

echo "selection_begin"
echo "sshd_disable_krb5=$sshd_disable_krb5"
echo "sshd_disable_pam=$sshd_disable_pam"
echo "selected_candidate=$selected"
sed -n '80,125p' "$root/etc/init.d/sshd" 2>/dev/null || true
if [ -f "$root/etc/conf.d/sshd" ]; then
    cat "$root/etc/conf.d/sshd" 2>/dev/null || true
fi
echo "selection_end"

run_config_test() {
    label="$1"
    candidate="$2"
    output="$work/$label-config.txt"
    : > "$output"
    chroot "$root" "$candidate" -t -f /etc/ssh/sshd_config > "$output" 2>&1 &
    pid=$!
    elapsed=0
    while kill -0 "$pid" 2>/dev/null && [ "$elapsed" -lt 15 ]; do
        sleep 1
        elapsed=$((elapsed + 1))
    done
    timed_out=no
    if kill -0 "$pid" 2>/dev/null; then
        timed_out=yes
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null
    rc=$?
    [ "$timed_out" = no ] || rc=124
    echo "variant_config_rc=$rc"
    echo "variant_config_timed_out=$timed_out"
    echo "variant_config_elapsed_seconds=$elapsed"
    echo "variant_config_output_begin"
    cat "$output" 2>/dev/null || true
    echo "variant_config_output_end"
}

run_listener_test() {
    label="$1"
    candidate="$2"
    output="$work/$label-listener.txt"
    pidfile="/run/a33x-$label-sshd.pid"
    : > "$output"
    chroot "$root" "$candidate" -D -e -ddd \
        -f /etc/ssh/sshd_config \
        -p 2222 \
        -o ListenAddress=127.0.0.1 \
        -o PidFile="$pidfile" > "$output" 2>&1 &
    pid=$!
    active_pid="$pid"
    elapsed=0
    while kill -0 "$pid" 2>/dev/null && [ "$elapsed" -lt 5 ]; do
        sleep 1
        elapsed=$((elapsed + 1))
    done
    survived=no
    if kill -0 "$pid" 2>/dev/null; then
        survived=yes
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null
    rc=$?
    active_pid=""
    echo "variant_listener_rc=$rc"
    echo "variant_listener_survived_5s=$survived"
    echo "variant_listener_elapsed_seconds=$elapsed"
    if grep -qiE 'server listening on|listening on .*port 2222' "$output"; then
        echo "variant_listener_log_says_listening=yes"
    else
        echo "variant_listener_log_says_listening=no"
    fi
    echo "variant_listener_output_begin"
    cat "$output" 2>/dev/null || true
    echo "variant_listener_output_end"
}

for candidate in /usr/sbin/sshd.krb5 /usr/sbin/sshd.pam /usr/sbin/sshd; do
    label="${candidate##*/}"
    echo "variant_begin=$label"
    if [ ! -x "$root$candidate" ]; then
        echo "variant_state=missing"
        echo "variant_end=$label"
        continue
    fi
    echo "variant_state=present"
    echo "variant_path=$candidate"
    echo "variant_sha256=$(sha256sum "$root$candidate" | awk '{print $1}')"
    echo "variant_bytes=$(stat -c '%s' "$root$candidate" 2>/dev/null || true)"
    echo "variant_mode=$(stat -c '%a' "$root$candidate" 2>/dev/null || true)"
    run_config_test "$label" "$candidate"
    run_listener_test "$label" "$candidate"
    echo "variant_end=$label"
done

echo "phone_partition_writes=no"
echo "phone_reboot_performed=no"

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
    stop = f"{end}\n"
    if text.count(start) != 1 or text.count(stop) != 1:
        return ""
    return text.split(start, 1)[1].split(stop, 1)[0]


def parse_variants(text: str) -> list[dict[str, object]]:
    starts = list(re.finditer(r"^variant_begin=(\S+)$", text, re.MULTILINE))
    variants: list[dict[str, object]] = []
    for match in starts:
        label = match.group(1)
        end_token = f"variant_end={label}"
        end = text.find(end_token, match.end())
        if end < 0:
            continue
        block = text[match.end() : end]
        variants.append(
            {
                "label": label,
                "state": value(block, "variant_state"),
                "path": value(block, "variant_path"),
                "sha256": value(block, "variant_sha256"),
                "bytes": value(block, "variant_bytes"),
                "mode": value(block, "variant_mode"),
                "config_rc": value(block, "variant_config_rc"),
                "config_timed_out": value(block, "variant_config_timed_out"),
                "config_elapsed_seconds": value(block, "variant_config_elapsed_seconds"),
                "config_output": section(
                    block,
                    "variant_config_output_begin",
                    "variant_config_output_end",
                ).splitlines()[-100:],
                "listener_rc": value(block, "variant_listener_rc"),
                "listener_survived_5s": value(block, "variant_listener_survived_5s"),
                "listener_elapsed_seconds": value(block, "variant_listener_elapsed_seconds"),
                "listener_log_says_listening": value(
                    block, "variant_listener_log_says_listening"
                ),
                "listener_output": section(
                    block,
                    "variant_listener_output_begin",
                    "variant_listener_output_end",
                ).splitlines()[-200:],
            }
        )
    return variants


def diagnose(selected: str, variants: list[dict[str, object]]) -> str:
    item = next((v for v in variants if v.get("path") == selected), None)
    if item is None or item.get("state") != "present":
        return "openrc-selected-sshd-variant-missing"
    if item.get("config_timed_out") == "yes":
        return "openrc-selected-sshd-config-test-hung"
    if item.get("config_rc") != "0":
        return "openrc-selected-sshd-config-test-failed"
    if item.get("listener_survived_5s") == "yes" and item.get(
        "listener_log_says_listening"
    ) == "yes":
        return "selected-sshd-listens-manually-openrc-startup-path-failed"
    return "openrc-selected-sshd-exits-before-listening"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test the exact installed A33 sshd variants and OpenRC-selected "
            "daemon in a read-only TWRP chroot"
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
        timeout=150,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise DiagnosticError(
            f"installed sshd variant diagnostic failed rc={completed.returncode}:\n"
            f"{output}\n{stderr}"
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
            raise DiagnosticError(f"missing diagnostic safety marker: {marker}")

    selected = value(section(output, "selection_begin", "selection_end"), "selected_candidate")
    variants = parse_variants(output)
    diagnosis = diagnose(selected, variants)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-sshd-variants-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "diagnostic.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    summary_values: dict[str, object] = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "diagnose-a33-installed-sshd-variants",
        "implementation_language": "python3",
        "adb_serial": serial,
        "twrp_recovery_sha256": values["recovery_sha"],
        "userdata_resolved": values["userdata_resolved"],
        "userdata_filesystem_uuid": uuid_value,
        "userdata_filesystem_label": label,
        "selected_candidate": selected,
        "variants": variants,
        "diagnosis": diagnosis,
        "readonly_root_mount_passed": True,
        "volatile_runtime_only": True,
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "raw_report": str(raw),
        "raw_report_sha256": sha256_file(raw),
        "diagnostic_status": "passed",
    }
    summary = out / "summary.json"
    summary.write_text(
        json.dumps(summary_values, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz", format=tarfile.PAX_FORMAT) as bundle:
        bundle.add(out, arcname=out.name)
    archive_sha = sha256_file(archive)
    archive.with_suffix(archive.suffix + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )

    print(json.dumps(summary_values, indent=2, sort_keys=True))
    print(f"diagnostic_directory={out}")
    print(f"diagnostic_archive={archive}")
    print(f"diagnostic_archive_sha256={archive_sha}")
    print("userdata_persistent_writes=no")
    print("phone_partition_writes=no")
    print("phone_reboot_performed=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DiagnosticError, common.Refusal, OSError, ValueError) as exc:
        print(f"SSHD VARIANT DIAGNOSTIC FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

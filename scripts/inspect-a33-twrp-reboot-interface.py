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

HERE = Path(__file__).resolve().parent
FINGERPRINT_PATH = HERE / "cleanup-a33-openrc-sshd-chroot-v2.py"
EXPECTED_FINGERPRINT_BLOB = "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"

spec = importlib.util.spec_from_file_location("a33_twrp_reboot_probe_fingerprint", FINGERPRINT_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load TWRP fingerprint helper: {FINGERPRINT_PATH}")
fingerprint = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fingerprint
spec.loader.exec_module(fingerprint)
common = fingerprint.common


class RebootProbeError(RuntimeError):
    pass


REMOTE_SCRIPT = r'''set -u
echo "boot_id=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)"
echo "uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null || true)"
echo "adbd_pid=$(pidof adbd 2>/dev/null || true)"
echo "twrp_command=$(command -v twrp 2>/dev/null || true)"
echo "reboot_command=$(command -v reboot 2>/dev/null || true)"
echo "toolbox_command=$(command -v toolbox 2>/dev/null || true)"
echo "toybox_command=$(command -v toybox 2>/dev/null || true)"
echo "getprop_command=$(command -v getprop 2>/dev/null || true)"
echo "setprop_command=$(command -v setprop 2>/dev/null || true)"
echo "sys_powerctl=$(getprop sys.powerctl 2>/dev/null || true)"
echo "ro_twrp_version=$(getprop ro.twrp.version 2>/dev/null || true)"
echo "twrp_help_begin"
if command -v twrp >/dev/null 2>&1; then
    twrp help 2>&1 || twrp --help 2>&1 || true
fi
echo "twrp_help_end"
echo "reboot_links_begin"
ls -l /sbin/reboot /system/bin/reboot /system/bin/twrp /sbin/twrp 2>&1 || true
echo "reboot_links_end"
echo "phone_partition_writes=no"
echo "phone_reboot_performed=no"
'''


def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def section(text: str, name: str) -> list[str]:
    begin = f"{name}_begin"
    end = f"{name}_end"
    result: list[str] = []
    active = False
    for line in text.replace("\r", "").splitlines():
        if line == begin:
            active = True
            continue
        if line == end:
            active = False
            continue
        if active:
            result.append(line)
    return result


def values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    active = False
    for line in text.replace("\r", "").splitlines():
        if line.endswith("_begin"):
            active = True
        elif line.endswith("_end"):
            active = False
        elif not active and "=" in line:
            key, value = line.split("=", 1)
            result.setdefault(key, value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect TWRP reboot interfaces without rebooting or writing the phone")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    actual = common.run(
        ["git", "-C", str(repo), "hash-object", str(FINGERPRINT_PATH)], check=False
    ).stdout.strip()
    if actual != EXPECTED_FINGERPRINT_BLOB:
        raise RebootProbeError(
            f"checked-in TWRP fingerprint helper changed: actual={actual!r} expected={EXPECTED_FINGERPRINT_BLOB!r}"
        )

    serial = common.select_recovery(adb, 30)
    runtime = fingerprint.validate_runtime_fingerprint(adb, serial)
    completed = common.run(
        [adb, "-s", serial, "shell", "sh", "-s"],
        input_data=REMOTE_SCRIPT,
        check=False,
        timeout=30,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise RebootProbeError(f"read-only TWRP reboot probe failed rc={completed.returncode}:\n{output}\n{stderr}")
    observed = values(output)
    help_lines = section(output, "twrp_help")
    link_lines = section(output, "reboot_links")
    help_text = "\n".join(help_lines)
    twrp_reboot_help = bool(re.search(r"(?:^|\s)reboot(?:\s|$)", help_text, re.IGNORECASE))
    if output.count("phone_partition_writes=no") != 1 or output.count("phone_reboot_performed=no") != 1:
        raise RebootProbeError("read-only marker contract did not complete")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-twrp-reboot-interface-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "probe.txt"
    raw.write_text(output + ("\n=== stderr ===\n" + stderr if stderr else ""), encoding="utf-8")
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "inspect-a33-twrp-reboot-interface-read-only",
        "implementation_language": "python3",
        "adb_serial": serial,
        "boot_id": observed.get("boot_id", ""),
        "uptime": observed.get("uptime", ""),
        "adbd_pid": observed.get("adbd_pid", ""),
        "twrp_command": observed.get("twrp_command", ""),
        "reboot_command": observed.get("reboot_command", ""),
        "setprop_command": observed.get("setprop_command", ""),
        "twrp_help_mentions_reboot": twrp_reboot_help,
        "twrp_help_lines": help_lines,
        "reboot_link_lines": link_lines,
        "twrp_kernel_release": runtime["kernel_release"],
        "twrp_config_gz_sha256": runtime["config_gz_sha256"],
        "recommended_reboot_command_status": (
            "twrp-cli-reboot-visible" if twrp_reboot_help and observed.get("twrp_command") else "requires-device-specific-resolution"
        ),
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "raw_report": str(raw),
        "raw_report_sha256": sha_file(raw),
        "inspection_status": "passed",
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"inspection_directory={out}")
    print("phone_partition_writes=no")
    print("phone_reboot_performed=no")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RebootProbeError, fingerprint.CleanupV2Error, common.Refusal, OSError, ValueError) as exc:
        print(f"A33 TWRP REBOOT INTERFACE INSPECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

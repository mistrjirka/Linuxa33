#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "cleanup-a33-openrc-sshd-chroot.py"
EXPECTED_BASE_BLOB = "bb5865f150369fdf2ce269cfc4b2bba107e7cfd0"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
EXPECTED_KERNEL_RELEASE = "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
EXPECTED_CONFIG_GZ_SHA256 = "7dd732d5b653571497e3e77d286705efc5b4247dcdc937afffc54827b4f3997c"
REQUIRED_CMDLINE_MARKERS = (
    "bootmode=2",
    "androidboot.hardware=s5e8825",
    "androidboot.serialno=RFCTA00V43L",
)

spec = importlib.util.spec_from_file_location("a33_openrc_sshd_cleanup_v2_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load cleanup base: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)
common = base.common


class CleanupV2Error(RuntimeError):
    pass


FINGERPRINT_SCRIPT = r'''set -u
echo "kernel_release=$(uname -r 2>/dev/null || true)"
echo "config_gz_sha256=$(sha256sum /proc/config.gz 2>/dev/null | awk 'NR==1 {print $1}')"
echo "kernel_cmdline=$(cat /proc/cmdline 2>/dev/null || true)"
echo "twrp_version=$(getprop ro.twrp.version 2>/dev/null || true)"
echo "recovery_path=$(readlink -f /dev/block/by-name/recovery 2>/dev/null || true)"
echo "recovery_path_state=$(ls -ld /dev/block/by-name/recovery 2>&1 || true)"
'''


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.replace("\r", "").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values.setdefault(key, value)
    return values


def validate_runtime_fingerprint(adb: str, serial: str) -> dict[str, str]:
    output = common.adb_shell(adb, serial, FINGERPRINT_SCRIPT)
    values = parse_values(output)
    mismatches: list[str] = []
    if values.get("kernel_release") != EXPECTED_KERNEL_RELEASE:
        mismatches.append(
            "kernel_release: "
            f"actual={values.get('kernel_release')!r} expected={EXPECTED_KERNEL_RELEASE!r}"
        )
    if values.get("config_gz_sha256") != EXPECTED_CONFIG_GZ_SHA256:
        mismatches.append(
            "config_gz_sha256: "
            f"actual={values.get('config_gz_sha256')!r} expected={EXPECTED_CONFIG_GZ_SHA256!r}"
        )
    cmdline = values.get("kernel_cmdline", "")
    for marker in REQUIRED_CMDLINE_MARKERS:
        if marker not in cmdline.split():
            mismatches.append(f"kernel_cmdline missing exact marker: {marker}")
    if mismatches:
        raise CleanupV2Error(
            "exact TWRP runtime fingerprint failed:\n" + "\n".join(mismatches)
        )
    return values


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Clean mounts/processes left by the disabled OpenRC SSH chroot diagnostic, "
            "with exact TWRP runtime-fingerprint fallback"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    repo = Path.home() / "Linuxa33"
    blob = common.run(
        ["git", "-C", str(repo), "hash-object", str(BASE_PATH)],
        check=False,
    ).stdout.strip()
    if blob != EXPECTED_BASE_BLOB:
        raise CleanupV2Error(
            f"cleanup base changed: actual={blob!r} expected={EXPECTED_BASE_BLOB!r}"
        )

    serial = common.select_recovery(adb, 30)
    live_values, _ = common.live_state(adb, serial)
    observed_recovery_sha = live_values.get("recovery_sha", "")
    fingerprint = validate_runtime_fingerprint(adb, serial)

    if observed_recovery_sha == EXPECTED_TWRP_SHA256:
        gate_source = "recovery-partition-sha256-and-runtime-fingerprint"
    elif observed_recovery_sha == "":
        gate_source = "runtime-fingerprint-recovery-path-unreadable"
    else:
        raise CleanupV2Error(
            "recovery partition is readable but does not match exact TWRP: "
            f"actual={observed_recovery_sha!r} expected={EXPECTED_TWRP_SHA256!r}"
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
            base.CHROOT_ROOT,
            base.CHROOT_WORK,
        ],
        input_data=base.REMOTE_SCRIPT,
        check=False,
        timeout=90,
    )
    output = completed.stdout.replace("\r", "")
    stderr = completed.stderr.replace("\r", "")
    if completed.returncode != 0:
        raise CleanupV2Error(
            f"OpenRC SSH chroot cleanup failed rc={completed.returncode}:\n"
            f"{output}\n{stderr}"
        )
    if "cleanup_status=passed" not in output:
        raise CleanupV2Error("cleanup did not report success")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-openrc-sshd-chroot-cleanup-v2-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "cleanup.txt"
    raw.write_text(
        output + ("\n=== stderr ===\n" + stderr if stderr else ""),
        encoding="utf-8",
    )
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "cleanup-disabled-a33-openrc-sshd-chroot-diagnostic-v2",
        "implementation_language": "python3",
        "adb_serial": serial,
        "twrp_gate_source": gate_source,
        "observed_recovery_partition_sha256": observed_recovery_sha,
        "twrp_kernel_release": fingerprint["kernel_release"],
        "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
        "twrp_version": fingerprint.get("twrp_version", ""),
        "recovery_path": fingerprint.get("recovery_path", ""),
        "recovery_path_state": fingerprint.get("recovery_path_state", ""),
        "cleanup_status": "passed",
        "mounts_before": base.section(output, "mounts_before_begin", "mounts_before_end"),
        "mounts_after": base.section(output, "mounts_after_begin", "mounts_after_end"),
        "chroot_processes_before": base.section(
            output, "chroot_processes_before_begin", "chroot_processes_before_end"
        ),
        "chroot_processes_after": base.section(
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
    except (CleanupV2Error, base.CleanupError, common.Refusal, OSError, ValueError) as exc:
        print(f"A33 OPENRC SSH CHROOT CLEANUP V2 FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

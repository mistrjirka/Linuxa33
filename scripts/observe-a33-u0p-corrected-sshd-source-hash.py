#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
FLASH_PATH = HERE / "flash-a33-u0p-corrected-sshd-source-hash.py"
U0O_OBSERVER_V2_PATH = HERE / "observe-a33-u0o-persistent-sshd-trace-v2.py"
EXPECTED_FLASH_BLOB = "793b82e81247654c7a2eb7200e130df56268fd83"
EXPECTED_U0O_OBSERVER_V2_BLOB = "4231f2f2c71d5ebfc008b9c5da156e4ffd392b9f"

OBSERVATION_SECONDS = 90
TWRP_REBOOT = "/system/bin/twrp"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_u0p_observer_flash", FLASH_PATH)
u0o_observer_v2 = load("a33_u0p_observer_u0o_v2", U0O_OBSERVER_V2_PATH)
helpers = u0o_observer_v2.base
common = flash.common


class U0pObserveError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_preflight(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (FLASH_PATH, EXPECTED_FLASH_BLOB),
        (U0O_OBSERVER_V2_PATH, EXPECTED_U0O_OBSERVER_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0pObserveError(
                f"checked-in U0p observer dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    report_path = root / "build/a33-u0p-corrected-sshd-source-hash-flash.txt"
    if not report_path.is_file():
        raise U0pObserveError(f"missing U0p flash report: {report_path}")
    report = common.kv(report_path)
    common.require(
        report,
        {
            "operation": "flash-exact-u0p-corrected-sshd-source-hash",
            "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
            "recovery_previous_sha256": common.KNOWN_TWRP_SHA256,
            "recovery_partition_sha256": flash.EXPECTED_CANDIDATE_SHA256,
            "rootfs_validation": (
                "identity-critical-hashes-exact-host-keys-and-known-u0o-failure-trace-passed"
            ),
            "persistent_trace_path": flash.TRACE_PATH,
            "persistent_trace_baseline": (
                "known-u0o-instrumented-source-hash-mismatch"
            ),
            "persistent_trace_baseline_sha256": (
                flash.KNOWN_U0O_FAILURE_TRACE_SHA256
            ),
            "userdata_written": "no",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "yes",
            "reboot_performed": "no",
            "flash_status": "passed",
        },
        "U0p flash report",
    )
    if common.sha_file(Path(local["manifest_path"])) != report.get("manifest_sha256"):
        raise U0pObserveError("U0p manifest changed after flashing")
    local["flash_report_path"] = report_path
    return local


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reboot exact TWRP into U0p with the TWRP CLI, prove the old ADB "
            "and USB instance disappeared, then observe the full 90-second window"
        )
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    ip_cmd = helpers.require_command("ip")
    lsusb_cmd = helpers.require_command("lsusb")
    ping_cmd = helpers.require_command("ping")

    local = local_preflight(root, repo)
    print("u0p_observer_local_preflight=passed")
    serial = common.select_recovery(adb, 30)
    flash.base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    flash.validate_phone_rootfs(adb, serial, local)
    recovery_state = flash.base.recovery_helper.prepare(
        common, adb, serial, flash.EXPECTED_CANDIDATE_SHA256
    )
    try:
        print("u0p_recovery_partition_readback=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0pObserveError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    reboot_values = helpers.verify_twrp_reboot_interface(adb, serial)
    old_boot_id = reboot_values["boot_id"]
    old_adbd_pid = reboot_values.get("adbd_pid", "")
    old_usb_line = helpers.require_single_usb_line(lsusb_cmd)
    print("twrp_native_reboot_interface=passed")
    print(f"preboot_boot_id={old_boot_id}")
    print(f"preboot_adbd_pid={old_adbd_pid}")
    print(f"preboot_usb_line={old_usb_line}")

    if args.preflight_only:
        print("u0p_observer_preflight_status=passed")
        print("phone_partition_writes=no")
        print("phone_reboot_performed=no")
        return 0

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0p-corrected-sshd-source-hash-observation-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "host-before.txt").write_text(
        helpers.u0n_observer.host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    (out / "preboot.json").write_text(
        json.dumps(
            {
                "boot_id": old_boot_id,
                "adbd_pid": old_adbd_pid,
                "usb_line": old_usb_line,
                "twrp_command": TWRP_REBOOT,
                "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
                "persistent_trace_baseline_sha256": (
                    flash.KNOWN_U0O_FAILURE_TRACE_SHA256
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    command_start_wall = datetime.now().astimezone()
    command_start = time.monotonic()
    reboot_result = helpers.run_host(
        [adb, "-s", serial, "shell", TWRP_REBOOT, "reboot", "recovery"],
        timeout=15,
    )
    (out / "twrp-reboot-command.txt").write_text(
        "command=/system/bin/twrp reboot recovery\n"
        f"returncode={reboot_result.returncode}\n"
        "stdout_begin\n"
        f"{reboot_result.stdout}"
        "stdout_end\n"
        "stderr_begin\n"
        f"{reboot_result.stderr}"
        "stderr_end\n",
        encoding="utf-8",
    )

    transition_elapsed, transition_rows = helpers.wait_for_transition(
        adb,
        serial,
        lsusb_cmd,
        old_boot_id,
        old_usb_line,
        out / "transition.jsonl",
        command_start,
    )
    if transition_elapsed is None:
        summary = {
            "created": helpers.now_iso(),
            "operation": "observe-u0p-corrected-sshd-source-hash-transition-proven-90s",
            "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
            "old_boot_id": old_boot_id,
            "old_adbd_pid": old_adbd_pid,
            "old_usb_line": old_usb_line,
            "transition_timeout_seconds": helpers.TRANSITION_TIMEOUT_SECONDS,
            "transition_samples": len(transition_rows),
            "reboot_transition_verified": False,
            "observation_started": False,
            "observation_status": (
                "failed-old-twrp-adb-or-usb-instance-never-disappeared"
            ),
            "phone_partition_writes": "no",
            "phone_reboot_command_attempted": "yes-twrp-cli-recovery",
        }
        (out / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        archive, archive_sha = helpers.write_archive(out)
        print(json.dumps(summary, indent=2, sort_keys=True))
        print(f"observation_archive={archive}")
        print(f"observation_archive_sha256={archive_sha}")
        print("U0p observation refused: old TWRP did not provably disappear.")
        return 4

    print(f"reboot_transition_verified_seconds={transition_elapsed:.3f}")
    observation_start = time.monotonic()
    observation_start_wall = datetime.now().astimezone()
    rows: list[dict[str, object]] = []
    first_usb: float | None = None
    first_interface: float | None = None
    first_ping: float | None = None
    first_refused: float | None = None
    first_banner: float | None = None
    tcp_counts: dict[str, int] = {}
    with (out / "observation.jsonl").open("w", encoding="utf-8") as stream:
        while True:
            elapsed = time.monotonic() - observation_start
            row = helpers.u0n_observer.sample(ip_cmd, lsusb_cmd, ping_cmd, elapsed)
            rows.append(row)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            if row["usb_enumeration"] and first_usb is None:
                first_usb = elapsed
            if row["host_usb_network_interface"] and first_interface is None:
                first_interface = elapsed
            if row["ping_172_16_42_1"] and first_ping is None:
                first_ping = elapsed
            state = str(row["tcp22_state"])
            tcp_counts[state] = tcp_counts.get(state, 0) + 1
            if state == "connection-refused" and first_refused is None:
                first_refused = elapsed
            if state == "ssh-banner" and first_banner is None:
                first_banner = elapsed
            if elapsed >= OBSERVATION_SECONDS:
                break
            time.sleep(0.5)

    finished_wall = datetime.now().astimezone()
    (out / "host-after.txt").write_text(
        helpers.u0n_observer.host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    journalctl = shutil.which("journalctl")
    if journalctl:
        journal = helpers.run_host(
            [
                journalctl,
                "-k",
                "--since",
                command_start_wall.isoformat(timespec="seconds"),
                "--until",
                finished_wall.isoformat(timespec="seconds"),
            ],
            timeout=20,
        )
        (out / "host-kernel-journal.txt").write_text(
            journal.stdout + journal.stderr, encoding="utf-8"
        )

    new_usb_lines = sorted(
        {
            str(row.get("usb_line", ""))
            for row in rows
            if row.get("usb_enumeration") and row.get("usb_line")
        }
    )
    summary = {
        "created": helpers.now_iso(),
        "operation": "observe-u0p-corrected-sshd-source-hash-transition-proven-90s",
        "implementation_language": "python3",
        "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
        "persistent_trace_path": flash.TRACE_PATH,
        "corrected_instrumented_sshd_sha256": flash.CORRECTED_SSHD_SHA256,
        "twrp_reboot_command": "/system/bin/twrp reboot recovery",
        "old_boot_id": old_boot_id,
        "old_adbd_pid": old_adbd_pid,
        "old_usb_line": old_usb_line,
        "reboot_transition_verified": True,
        "reboot_transition_seconds": round(transition_elapsed, 3),
        "transition_samples": len(transition_rows),
        "observation_started": observation_start_wall.isoformat(timespec="microseconds"),
        "observation_seconds": OBSERVATION_SECONDS,
        "observation_rows": len(rows),
        "new_usb_lines": new_usb_lines,
        "first_usb_seconds": first_usb,
        "first_interface_seconds": first_interface,
        "first_ping_seconds": first_ping,
        "first_connection_refused_seconds": first_refused,
        "first_ssh_banner_seconds": first_banner,
        "ping_ever": first_ping is not None,
        "ssh_banner_ever": first_banner is not None,
        "tcp22_state_counts": tcp_counts,
        "phone_partition_writes": "no",
        "phone_reboot_performed": "yes-twrp-cli-recovery",
        "observation_status": "passed-transition-proven-full-90-second-window",
        "next_action": "enter-download-mode-and-restore-exact-twrp-immediately",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive, archive_sha = helpers.write_archive(out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"observation_directory={out}")
    print(f"observation_archive={archive}")
    print(f"observation_archive_sha256={archive_sha}")
    print("NEXT: enter Samsung Download Mode and restore exact TWRP immediately.")
    print("python3 scripts/restore-a33-twrp-odin.py RESTORE-EXACT-TWRP")
    print("After Odin, boot TWRP directly; do not boot Android or U0p again.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0pObserveError,
        flash.U0pFlashError,
        flash.u0o_flash.U0oFlashError,
        flash.u0o_flash.u0n_flash_v2.U0nFlashV2Error,
        flash.base.U0nFlashError,
        flash.base.restore.RestoreError,
        flash.base.restore.cleanup.CleanupV2Error,
        flash.base.restore.block_helper.ExactBlockNodeError,
        flash.base.restore.identity_helper.Ext4IdentityError,
        flash.base.recovery_helper.ExactRecoveryNodeError,
        common.Refusal,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
    ) as exc:
        print(f"U0p OBSERVER FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

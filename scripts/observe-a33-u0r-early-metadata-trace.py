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
FLASH_PATH = HERE / "flash-a33-u0r-early-metadata-trace.py"
U0P_OBSERVER_PATH = HERE / "observe-a33-u0p-corrected-sshd-source-hash.py"
EXPECTED_FLASH_BLOB = "36923334243058f2f836b5cf3710b0c642bac2f8"
EXPECTED_U0P_OBSERVER_BLOB = "ab35fa03ae34a48bf1e902eb3b7d91dac951c011"
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


flash = load("a33_u0r_observer_flash", FLASH_PATH)
u0p_observer = load("a33_u0r_observer_u0p", U0P_OBSERVER_PATH)
helpers = u0p_observer.helpers
common = flash.common


class U0rObserveError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_preflight(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (FLASH_PATH, EXPECTED_FLASH_BLOB),
        (U0P_OBSERVER_PATH, EXPECTED_U0P_OBSERVER_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0rObserveError(
                f"checked-in U0r observer dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    report_path = root / "build" / flash.REPORT_NAME
    if not report_path.is_file():
        raise U0rObserveError(f"missing U0r flash report: {report_path}")
    report = common.kv(report_path)
    common.require(
        report,
        {
            "operation": "flash-exact-u0r-early-metadata-trace",
            "candidate_sha256": str(local["candidate_sha"]),
            "manifest_sha256": str(local["manifest_sha"]),
            "patch_report_sha256": str(local["patch_sha"]),
            "recovery_previous_sha256": common.KNOWN_TWRP_SHA256,
            "recovery_partition_sha256": str(local["candidate_sha"]),
            "metadata_trace_baseline": "all-three-u0r-files-absent",
            "runtime_metadata_writes_expected": "yes-three-diagnostic-files",
            "userdata_written": "no",
            "metadata_written_by_flash": "no",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "yes",
            "reboot_performed": "no",
            "flash_status": "passed",
        },
        "U0r flash report",
    )
    local["flash_report_path"] = report_path
    return local


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove TWRP-to-U0r transition and observe the full metadata-trace boot window"
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
    print("u0r_observer_local_preflight=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    serial = common.select_recovery(adb, 30)
    flash.base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    flash.parent.validate_phone_rootfs(adb, serial, local)
    recovery_state = flash.base.recovery_helper.prepare(
        common, adb, serial, str(local["candidate_sha"])
    )
    try:
        print("u0r_recovery_partition_readback=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0rObserveError("temporary recovery node cleanup failed")
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
        print("u0r_observer_preflight_status=passed")
        print("phone_partition_writes=no")
        print("phone_reboot_performed=no")
        return 0

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0r-early-metadata-trace-observation-{timestamp}"
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
                "candidate_sha256": local["candidate_sha"],
                "metadata_trace_path": f"/{flash.builder.TRACE_RELATIVE}",
                "metadata_hook04_path": f"/{flash.builder.HOOK04_RELATIVE}",
                "metadata_hook05_path": f"/{flash.builder.HOOK05_RELATIVE}",
                "twrp_command": TWRP_REBOOT,
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
        raise U0rObserveError(
            "old TWRP ADB or exact USB instance never provably disappeared"
        )
    print(f"reboot_transition_verified_seconds={transition_elapsed:.3f}")

    observation_start = time.monotonic()
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

    summary = {
        "created": helpers.now_iso(),
        "operation": "observe-u0r-early-metadata-trace-transition-proven-90s",
        "implementation_language": "python3",
        "candidate_sha256": local["candidate_sha"],
        "metadata_trace_path": f"/{flash.builder.TRACE_RELATIVE}",
        "metadata_hook04_path": f"/{flash.builder.HOOK04_RELATIVE}",
        "metadata_hook05_path": f"/{flash.builder.HOOK05_RELATIVE}",
        "runtime_metadata_writes_expected": True,
        "twrp_reboot_command": "/system/bin/twrp reboot recovery",
        "old_boot_id": old_boot_id,
        "old_adbd_pid": old_adbd_pid,
        "old_usb_line": old_usb_line,
        "reboot_transition_verified": True,
        "reboot_transition_seconds": round(transition_elapsed, 3),
        "transition_samples": len(transition_rows),
        "observation_seconds": OBSERVATION_SECONDS,
        "observation_rows": len(rows),
        "first_usb_seconds": first_usb,
        "first_interface_seconds": first_interface,
        "first_ping_seconds": first_ping,
        "first_connection_refused_seconds": first_refused,
        "first_ssh_banner_seconds": first_banner,
        "ping_ever": first_ping is not None,
        "ssh_banner_ever": first_banner is not None,
        "tcp22_state_counts": tcp_counts,
        "phone_partition_writes_by_observer": "no",
        "phone_reboot_performed": "yes-twrp-cli-recovery",
        "observation_status": "passed-transition-proven-full-90-second-window",
        "next_action": "restore-exact-twrp-then-collect-current-persistent-state-v2",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive, archive_sha = helpers.write_archive(out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"observation_directory={out}")
    print(f"observation_archive={archive}")
    print(f"observation_archive_sha256={archive_sha}")
    print("NEXT: enter Samsung Download Mode and restore exact TWRP.")
    print("python3 scripts/restore-a33-twrp-odin.py RESTORE-EXACT-TWRP")
    print("After restoring and booting TWRP directly, disable MTP and unmount Data.")
    print("python3 scripts/collect-a33-current-persistent-state-v2.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0rObserveError,
        flash.U0rFlashError,
        flash.parent.U0qV2FlashError,
        flash.parent.u0p_flash.U0pFlashError,
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
        print(f"U0r OBSERVER FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

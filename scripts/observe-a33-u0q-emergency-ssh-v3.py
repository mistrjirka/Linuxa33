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
FLASH_V3_PATH = HERE / "flash-a33-u0q-emergency-ssh-v3.py"
OBSERVER_V2_PATH = HERE / "observe-a33-u0q-emergency-ssh-v2.py"
EXPECTED_FLASH_V3_BLOB = "79e8b0dd2a2a781018b027b551f54796e4608afb"
EXPECTED_OBSERVER_V2_BLOB = "1b8bcc6a917214cd89abdc38c10af1f2a42ff449"
TWRP_REBOOT = "/system/bin/twrp"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_u0q_v3_observer_flash", FLASH_V3_PATH)
v2_observer = load("a33_u0q_v3_observer_parent", OBSERVER_V2_PATH)
helpers = v2_observer.helpers
common = flash.common


class U0qV3ObserveError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def local_preflight(root: Path, repo: Path) -> dict[str, object]:
    for path, expected in (
        (FLASH_V3_PATH, EXPECTED_FLASH_V3_BLOB),
        (OBSERVER_V2_PATH, EXPECTED_OBSERVER_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0qV3ObserveError(
                f"checked-in U0q v3 observer dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    report_path = root / "build/a33-u0q-v3-emergency-ssh-flash.txt"
    if not report_path.is_file():
        raise U0qV3ObserveError(f"missing U0q v3 flash report: {report_path}")
    report = common.kv(report_path)
    common.require(
        report,
        {
            "operation": "flash-exact-u0q-v3-emergency-ssh",
            "candidate_sha256": str(local["candidate_sha"]),
            "manifest_sha256": str(local["manifest_sha"]),
            "patch_report_sha256": str(local["patch_sha"]),
            "base_audit_sha256": str(local["base_audit_sha"]),
            "audit_v3_sha256": str(local["audit_v3_sha"]),
            "emergency_client_private_key_sha256": str(local["private_key_sha"]),
            "emergency_client_public_key_sha256": str(local["public_key_sha"]),
            "recovery_previous_sha256": common.KNOWN_TWRP_SHA256,
            "recovery_partition_sha256": str(local["candidate_sha"]),
            "rootfs_validation": (
                "identity-critical-hashes-exact-host-keys-and-known-u0p-trace-passed"
            ),
            "parent_trace_baseline": "known-u0p-openrc-script-loaded-boundary",
            "parent_trace_baseline_sha256": flash.v2_flash.KNOWN_U0P_TRACE_SHA256,
            "emergency_trace_baseline": "absent",
            "emergency_sshd_port": "2222",
            "runtime_mount_policy": flash.builder_v3.MOUNT_POLICY,
            "pre_switch_root_gate": "network-address-and-port-2222-listener",
            "userdata_written": "no",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "yes",
            "reboot_performed": "no",
            "flash_status": "passed",
        },
        "U0q v3 flash report",
    )
    local["flash_report_path"] = report_path
    return local


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reboot exact TWRP into U0q v3, prove transition, authenticate to "
            "port 2222 and collect staged live state"
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
    ssh = helpers.require_command("ssh")
    ip_cmd = helpers.require_command("ip")
    lsusb_cmd = helpers.require_command("lsusb")
    ping_cmd = helpers.require_command("ping")

    local = local_preflight(root, repo)
    print("u0q_v3_observer_local_preflight=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print(f"emergency_client_private_key={local['private_key']}")
    serial = common.select_recovery(adb, 30)
    flash.base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    flash.v2_flash.validate_phone_rootfs(adb, serial, local)
    recovery_state = flash.base.recovery_helper.prepare(
        common, adb, serial, str(local["candidate_sha"])
    )
    try:
        print("u0q_v3_recovery_partition_readback=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0qV3ObserveError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    reboot_values = helpers.verify_twrp_reboot_interface(adb, serial)
    old_boot_id = reboot_values["boot_id"]
    old_adbd_pid = reboot_values.get("adbd_pid", "")
    old_usb_line = helpers.require_single_usb_line(lsusb_cmd)
    print("twrp_native_reboot_interface=passed")
    print(f"preboot_boot_id={old_boot_id}")
    print(f"preboot_adbd_pid={old_adbd_pid}")
    print(f"preboot_usb_line={old_usb_line}")

    private_key = Path(local["private_key"])
    ssh_args = v2_observer.ssh_base_args(ssh, private_key)
    connect_command = " ".join(
        subprocess.list2cmdline([value]) for value in ssh_args
    )
    if args.preflight_only:
        print("u0q_v3_observer_preflight_status=passed")
        print(f"emergency_ssh_command={connect_command}")
        print("phone_partition_writes=no")
        print("phone_reboot_performed=no")
        return 0

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0q-v3-emergency-ssh-observation-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "host-before.txt").write_text(
        helpers.u0n_observer.host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    (out / "ssh-client-command.txt").write_text(connect_command + "\n", encoding="utf-8")
    (out / "preboot.json").write_text(
        json.dumps(
            {
                "boot_id": old_boot_id,
                "adbd_pid": old_adbd_pid,
                "usb_line": old_usb_line,
                "candidate_sha256": local["candidate_sha"],
                "private_key_sha256": local["private_key_sha"],
                "emergency_port": v2_observer.EMERGENCY_PORT,
                "runtime_mount_policy": flash.builder_v3.MOUNT_POLICY,
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
        raise U0qV3ObserveError(
            "old TWRP ADB or exact USB instance never provably disappeared"
        )
    print(f"reboot_transition_verified_seconds={transition_elapsed:.3f}")

    observation_start = time.monotonic()
    rows: list[dict[str, object]] = []
    first_usb: float | None = None
    first_interface: float | None = None
    first_ping: float | None = None
    first_banner: float | None = None
    first_auth: float | None = None
    auth_attempts = 0
    last_auth_attempt = -v2_observer.AUTH_RETRY_SECONDS
    auth_details: list[str] = []
    diagnostic_rc: int | None = None
    diagnostic_stderr = ""

    with (out / "observation.jsonl").open("w", encoding="utf-8") as stream:
        while True:
            elapsed = time.monotonic() - observation_start
            row = helpers.u0n_observer.sample(ip_cmd, lsusb_cmd, ping_cmd, elapsed)
            emergency_state = v2_observer.probe_banner()
            row["tcp2222_state"] = emergency_state
            rows.append(row)
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()

            if row["usb_enumeration"] and first_usb is None:
                first_usb = elapsed
            if row["host_usb_network_interface"] and first_interface is None:
                first_interface = elapsed
            if row["ping_172_16_42_1"] and first_ping is None:
                first_ping = elapsed
            if emergency_state == "ssh-banner" and first_banner is None:
                first_banner = elapsed

            if (
                emergency_state == "ssh-banner"
                and first_auth is None
                and elapsed - last_auth_attempt >= v2_observer.AUTH_RETRY_SECONDS
            ):
                last_auth_attempt = elapsed
                auth_attempts += 1
                try:
                    authenticated, detail = v2_observer.probe_auth(ssh, private_key)
                except subprocess.TimeoutExpired:
                    authenticated = False
                    detail = "probe_auth_timeout=yes\n"
                auth_details.append(
                    f"attempt={auth_attempts} elapsed={elapsed:.3f}\n{detail}"
                )
                (out / "ssh-auth-attempts.txt").write_text(
                    "\n".join(auth_details), encoding="utf-8"
                )
                if authenticated:
                    first_auth = elapsed
                    diagnostic_rc, diagnostic_stderr = (
                        v2_observer.capture_live_diagnostics(
                            ssh,
                            private_key,
                            out / "live-diagnostics.txt",
                        )
                    )
                    break

            if elapsed >= v2_observer.MAX_OBSERVATION_SECONDS:
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

    tcp2222_counts: dict[str, int] = {}
    tcp22_counts: dict[str, int] = {}
    for row in rows:
        state2222 = str(row.get("tcp2222_state", "unknown"))
        tcp2222_counts[state2222] = tcp2222_counts.get(state2222, 0) + 1
        state22 = str(row.get("tcp22_state", "unknown"))
        tcp22_counts[state22] = tcp22_counts.get(state22, 0) + 1

    if first_auth is not None and diagnostic_rc == 0:
        status = "passed-transition-proven-u0q-v3-emergency-ssh-authenticated-live-diagnostics-captured"
    elif first_auth is not None:
        status = "partial-u0q-v3-emergency-ssh-authenticated-diagnostic-command-failed"
    elif first_banner is not None:
        status = "failed-u0q-v3-emergency-ssh-banner-visible-authentication-never-succeeded"
    else:
        status = "failed-u0q-v3-emergency-ssh-banner-never-visible"

    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "observe-u0q-v3-emergency-ssh-live",
        "implementation_language": "python3",
        "candidate_sha256": local["candidate_sha"],
        "manifest_sha256": local["manifest_sha"],
        "audit_v3_sha256": local["audit_v3_sha"],
        "runtime_mount_policy": flash.builder_v3.MOUNT_POLICY,
        "emergency_client_private_key_sha256": local["private_key_sha"],
        "emergency_trace_path": flash.v2_flash.EMERGENCY_TRACE_PATH,
        "emergency_port": v2_observer.EMERGENCY_PORT,
        "twrp_reboot_command": "/system/bin/twrp reboot recovery",
        "old_boot_id": old_boot_id,
        "old_adbd_pid": old_adbd_pid,
        "old_usb_line": old_usb_line,
        "reboot_transition_verified": True,
        "reboot_transition_seconds": round(transition_elapsed, 3),
        "transition_samples": len(transition_rows),
        "observation_seconds": round(time.monotonic() - observation_start, 3),
        "observation_rows": len(rows),
        "first_usb_seconds": first_usb,
        "first_interface_seconds": first_interface,
        "first_ping_seconds": first_ping,
        "first_emergency_ssh_banner_seconds": first_banner,
        "first_emergency_ssh_auth_seconds": first_auth,
        "emergency_ssh_auth_attempts": auth_attempts,
        "live_diagnostics_returncode": diagnostic_rc,
        "live_diagnostics_stderr": diagnostic_stderr[-4000:],
        "tcp2222_state_counts": tcp2222_counts,
        "tcp22_state_counts": tcp22_counts,
        "emergency_ssh_command": connect_command,
        "phone_partition_writes": "no",
        "phone_reboot_performed": "yes-twrp-cli-recovery",
        "observation_status": status,
        "next_action": (
            "keep-u0q-v3-running-and-analyze-live-diagnostics"
            if first_auth is not None
            else "enter-download-mode-and-restore-exact-twrp-then-collect-trace"
        ),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive, archive_sha = helpers.write_archive(out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"observation_directory={out}")
    print(f"observation_archive={archive}")
    print(f"observation_archive_sha256={archive_sha}")
    if first_auth is not None:
        print("U0q v3 emergency SSH is authenticated. Keep the phone running.")
        print(f"CONNECT: {connect_command}")
        print("Do not boot Android. Restore exact TWRP after live diagnosis.")
        return 0 if diagnostic_rc == 0 else 5

    print("Emergency SSH was not authenticated. Enter Download Mode and restore TWRP.")
    print("python3 scripts/restore-a33-twrp-odin.py RESTORE-EXACT-TWRP")
    return 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0qV3ObserveError,
        flash.U0qV3FlashError,
        flash.v2_flash.U0qV2FlashError,
        flash.v2_flash.u0p_flash.U0pFlashError,
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
        print(f"U0q V3 OBSERVER FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

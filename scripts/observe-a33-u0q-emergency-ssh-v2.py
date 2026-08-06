#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
FLASH_PATH = HERE / "flash-a33-u0q-emergency-ssh-v2.py"
U0P_OBSERVER_PATH = HERE / "observe-a33-u0p-corrected-sshd-source-hash.py"
EXPECTED_FLASH_BLOB = "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
EXPECTED_U0P_OBSERVER_BLOB = "ab35fa03ae34a48bf1e902eb3b7d91dac951c011"

PHONE_HOST = "172.16.42.1"
EMERGENCY_PORT = 2222
MAX_OBSERVATION_SECONDS = 180
AUTH_RETRY_SECONDS = 1.0
TWRP_REBOOT = "/system/bin/twrp"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_u0q_v2_observer_flash", FLASH_PATH)
u0p_observer = load("a33_u0q_v2_observer_u0p", U0P_OBSERVER_PATH)
helpers = u0p_observer.helpers
common = flash.common


class U0qV2ObserveError(RuntimeError):
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
            raise U0qV2ObserveError(
                f"checked-in U0q v2 observer dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    report_path = root / "build/a33-u0q-v2-emergency-ssh-flash.txt"
    if not report_path.is_file():
        raise U0qV2ObserveError(f"missing U0q v2 flash report: {report_path}")
    report = common.kv(report_path)
    common.require(
        report,
        {
            "operation": "flash-exact-u0q-v2-emergency-ssh",
            "candidate_sha256": str(local["candidate_sha"]),
            "manifest_sha256": str(local["manifest_sha"]),
            "patch_report_sha256": str(local["patch_sha"]),
            "base_audit_sha256": str(local["base_audit_sha"]),
            "audit_v2_sha256": str(local["audit_v2_sha"]),
            "emergency_client_private_key_sha256": str(local["private_key_sha"]),
            "emergency_client_public_key_sha256": str(local["public_key_sha"]),
            "recovery_previous_sha256": common.KNOWN_TWRP_SHA256,
            "recovery_partition_sha256": str(local["candidate_sha"]),
            "rootfs_validation": (
                "identity-critical-hashes-exact-host-keys-and-known-u0p-trace-passed"
            ),
            "parent_trace_baseline": "known-u0p-openrc-script-loaded-boundary",
            "parent_trace_baseline_sha256": flash.KNOWN_U0P_TRACE_SHA256,
            "emergency_trace_baseline": "absent",
            "emergency_sshd_port": "2222",
            "pre_switch_root_gate": "network-address-and-port-2222-listener",
            "userdata_written": "no",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "yes",
            "reboot_performed": "no",
            "flash_status": "passed",
        },
        "U0q v2 flash report",
    )
    local["flash_report_path"] = report_path
    return local


def probe_banner(host: str = PHONE_HOST, port: int = EMERGENCY_PORT) -> str:
    try:
        with socket.create_connection((host, port), timeout=0.75) as connection:
            connection.settimeout(0.75)
            banner = connection.recv(256)
    except ConnectionRefusedError:
        return "connection-refused"
    except TimeoutError:
        return "connect-timeout"
    except OSError as exc:
        return f"connect-error-{exc.errno}"
    if banner.startswith(b"SSH-"):
        return "ssh-banner"
    if banner:
        return "connected-non-ssh-data"
    return "connected-no-banner"


def ssh_base_args(ssh: str, private_key: Path) -> list[str]:
    return [
        ssh,
        "-i",
        str(private_key),
        "-p",
        str(EMERGENCY_PORT),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=3",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=5",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "LogLevel=ERROR",
        f"root@{PHONE_HOST}",
    ]


def probe_auth(ssh: str, private_key: Path) -> tuple[bool, str]:
    completed = subprocess.run(
        ssh_base_args(ssh, private_key) + ["printf U0Q_AUTH_OK"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=8,
        check=False,
    )
    detail = (
        f"returncode={completed.returncode}\n"
        f"stdout={completed.stdout!r}\n"
        f"stderr={completed.stderr!r}\n"
    )
    return completed.returncode == 0 and completed.stdout == "U0Q_AUTH_OK", detail


REMOTE_DIAGNOSTIC_SCRIPT = r'''set +e
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
snapshot()
{
    label="$1"
    echo "===== U0Q_SNAPSHOT_BEGIN label=$label ====="
    echo "snapshot_label=$label"
    echo "uptime=$(cut -d' ' -f1 /proc/uptime 2>/dev/null)"
    echo "kernel=$(uname -a 2>/dev/null)"
    echo "identity=$(id 2>/dev/null)"
    echo "pid1_cmdline=$(tr '\000' ' ' < /proc/1/cmdline 2>/dev/null)"
    echo "pid1_wchan=$(cat /proc/1/wchan 2>/dev/null)"
    echo "pid1_status_begin"
    sed -n '1,80p' /proc/1/status 2>/dev/null
    echo "pid1_status_end"

    echo "processes_begin"
    ps -ef 2>/dev/null || ps 2>/dev/null || true
    echo "processes_end"
    echo "process_wchan_begin"
    for p in /proc/[0-9]*; do
        [ -r "$p/stat" ] || continue
        pid="${p##*/}"
        comm="$(cat "$p/comm" 2>/dev/null)"
        wchan="$(cat "$p/wchan" 2>/dev/null)"
        state="$(awk '/^State:/ {print $2}' "$p/status" 2>/dev/null)"
        printf 'pid=%s state=%s wchan=%s comm=%s\n' "$pid" "$state" "$wchan" "$comm"
    done
    echo "process_wchan_end"

    echo "openrc_status_begin"
    command -v rc-status >/dev/null 2>&1 && rc-status -a 2>&1
    command -v rc-status >/dev/null 2>&1 && rc-status -s 2>&1
    command -v rc-service >/dev/null 2>&1 && rc-service sshd status 2>&1
    echo "openrc_status_end"
    echo "openrc_runtime_begin"
    find /run/openrc -maxdepth 3 -type f -o -type d -o -type l 2>/dev/null | sort
    for f in /run/openrc/softlevel /run/openrc/started/* /run/openrc/starting/* /run/openrc/stopping/* /run/openrc/failed/*; do
        [ -e "$f" ] || continue
        printf '%s=' "$f"
        cat "$f" 2>/dev/null
    done
    echo "openrc_runtime_end"
    echo "default_runlevel_begin"
    ls -la /etc/runlevels/default 2>/dev/null
    echo "default_runlevel_end"

    echo "network_begin"
    ip -details address 2>&1 || true
    ip route 2>&1 || true
    ss -lntup 2>&1 || netstat -lntup 2>&1 || true
    echo "network_end"
    echo "firewall_begin"
    nft -a list ruleset 2>&1 || true
    echo "firewall_end"

    echo "mounts_begin"
    cat /proc/mounts 2>/dev/null
    echo "mounts_end"
    echo "mountinfo_begin"
    cat /proc/1/mountinfo 2>/dev/null
    echo "mountinfo_end"
    echo "cgroups_begin"
    cat /proc/cgroups 2>/dev/null
    find /sys/fs/cgroup -maxdepth 3 -type f -o -type d 2>/dev/null | sort | head -n 1000
    echo "cgroups_end"

    echo "emergency_trace_begin"
    cat /var/log/a33x-u0q-emergency-ssh.log 2>/dev/null
    echo "emergency_trace_end"
    echo "inherited_trace_begin"
    cat /var/log/a33x-u0o-real-boot-sshd.log 2>/dev/null
    echo "inherited_trace_end"
    echo "dmesg_tail_begin"
    dmesg 2>/dev/null | tail -n 500
    echo "dmesg_tail_end"
    echo "===== U0Q_SNAPSHOT_END label=$label ====="
}

snapshot t0
sleep 2
snapshot t2
sleep 3
snapshot t5
sleep 5
snapshot t10
sleep 10
snapshot t20
sleep 20
snapshot t40
'''


def capture_live_diagnostics(
    ssh: str, private_key: Path, destination: Path
) -> tuple[int, str]:
    completed = subprocess.run(
        ssh_base_args(ssh, private_key) + ["sh", "-s"],
        input=REMOTE_DIAGNOSTIC_SCRIPT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=70,
        check=False,
    )
    destination.write_text(completed.stdout, encoding="utf-8")
    stderr_path = destination.with_suffix(".stderr.txt")
    stderr_path.write_text(completed.stderr, encoding="utf-8")
    return completed.returncode, completed.stderr


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reboot exact TWRP into U0q v2, prove transition, authenticate to "
            "the independent port-2222 channel and collect staged live state"
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
    print("u0q_v2_observer_local_preflight=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print(f"emergency_client_private_key={local['private_key']}")
    serial = common.select_recovery(adb, 30)
    flash.base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    flash.validate_phone_rootfs(adb, serial, local)
    recovery_state = flash.base.recovery_helper.prepare(
        common, adb, serial, str(local["candidate_sha"])
    )
    try:
        print("u0q_v2_recovery_partition_readback=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0qV2ObserveError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    reboot_values = helpers.verify_twrp_reboot_interface(adb, serial)
    old_boot_id = reboot_values["boot_id"]
    old_adbd_pid = reboot_values.get("adbd_pid", "")
    old_usb_line = helpers.require_single_usb_line(lsusb_cmd)
    print("twrp_native_reboot_interface=passed")
    print(f"preboot_boot_id={old_boot_id}")
    print(f"preboot_adbd_pid={old_adbd_pid}")
    print(f"preboot_usb_line={old_usb_line}")

    connect_command = " ".join(
        subprocess.list2cmdline([value]) for value in ssh_base_args(ssh, Path(local["private_key"]))
    )
    if args.preflight_only:
        print("u0q_v2_observer_preflight_status=passed")
        print(f"emergency_ssh_command={connect_command}")
        print("phone_partition_writes=no")
        print("phone_reboot_performed=no")
        return 0

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0q-v2-emergency-ssh-observation-{timestamp}"
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
                "emergency_port": EMERGENCY_PORT,
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
        raise U0qV2ObserveError(
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
    last_auth_attempt = -AUTH_RETRY_SECONDS
    auth_details: list[str] = []
    diagnostic_rc: int | None = None
    diagnostic_stderr = ""

    with (out / "observation.jsonl").open("w", encoding="utf-8") as stream:
        while True:
            elapsed = time.monotonic() - observation_start
            row = helpers.u0n_observer.sample(ip_cmd, lsusb_cmd, ping_cmd, elapsed)
            emergency_state = probe_banner()
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
                and elapsed - last_auth_attempt >= AUTH_RETRY_SECONDS
            ):
                last_auth_attempt = elapsed
                auth_attempts += 1
                try:
                    authenticated, detail = probe_auth(
                        ssh, Path(local["private_key"])
                    )
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
                    diagnostic_rc, diagnostic_stderr = capture_live_diagnostics(
                        ssh,
                        Path(local["private_key"]),
                        out / "live-diagnostics.txt",
                    )
                    break

            if elapsed >= MAX_OBSERVATION_SECONDS:
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
        status = "passed-transition-proven-emergency-ssh-authenticated-live-diagnostics-captured"
    elif first_auth is not None:
        status = "partial-emergency-ssh-authenticated-diagnostic-command-failed"
    elif first_banner is not None:
        status = "failed-emergency-ssh-banner-visible-authentication-never-succeeded"
    else:
        status = "failed-emergency-ssh-banner-never-visible"

    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "observe-u0q-v2-emergency-ssh-live",
        "implementation_language": "python3",
        "candidate_sha256": local["candidate_sha"],
        "manifest_sha256": local["manifest_sha"],
        "audit_v2_sha256": local["audit_v2_sha"],
        "emergency_client_private_key_sha256": local["private_key_sha"],
        "emergency_trace_path": flash.EMERGENCY_TRACE_PATH,
        "emergency_port": EMERGENCY_PORT,
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
            "keep-u0q-running-and-analyze-live-diagnostics"
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
        print("U0q emergency SSH is authenticated. Keep the phone running for analysis.")
        print(f"CONNECT: {connect_command}")
        print("Do not boot Android. Restore exact TWRP after live diagnosis is complete.")
        return 0 if diagnostic_rc == 0 else 5

    print("Emergency SSH was not authenticated. Enter Download Mode and restore exact TWRP.")
    print("python3 scripts/restore-a33-twrp-odin.py RESTORE-EXACT-TWRP")
    return 4


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0qV2ObserveError,
        flash.U0qV2FlashError,
        flash.u0p_flash.U0pFlashError,
        flash.u0p_flash.u0o_flash.U0oFlashError,
        flash.u0p_flash.u0o_flash.u0n_flash_v2.U0nFlashV2Error,
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
        print(f"U0q V2 OBSERVER FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

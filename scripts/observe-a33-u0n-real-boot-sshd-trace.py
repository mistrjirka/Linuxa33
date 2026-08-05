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
import tarfile
import time

HERE = Path(__file__).resolve().parent
FLASH_PATH = HERE / "flash-a33-u0n-real-boot-sshd-trace.py"
EXPECTED_FLASH_BLOB = "35caa92b0271c2d0b01460db62c30ecfb0208ddc"
PHONE_IP = "172.16.42.1"
HOST_CIDR = "172.16.42.2/24"
USB_ID = "04e8:6860"
OBSERVATION_SECONDS = 90

spec = importlib.util.spec_from_file_location("a33_u0n_observer_flash", FLASH_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0n flash path: {FLASH_PATH}")
flash = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = flash
spec.loader.exec_module(flash)
common = flash.common


class U0nObserveError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def run_host(args: list[str], *, timeout: float = 5) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        return subprocess.CompletedProcess(args, 124, stdout, stderr + "\ntimeout\n")


def require_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise U0nObserveError(f"missing required host command: {name}")
    return path


def tcp_state(host: str, port: int = 22, timeout: float = 0.75) -> tuple[str, str]:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        result = sock.connect_ex((host, port))
        if result != 0:
            if result in {111, 61, 10061}:
                return "connection-refused", ""
            return f"connect-error-{result}", ""
        try:
            data = sock.recv(128)
        except socket.timeout:
            return "accepted-no-banner-timeout", ""
        text = data.decode("ascii", "replace").strip()
        if data.startswith(b"SSH-"):
            return "ssh-banner", text
        if data:
            return "accepted-non-ssh-data", text
        return "accepted-closed-no-data", ""
    except socket.timeout:
        return "connect-timeout", ""
    except OSError as exc:
        return f"socket-error-{exc.errno or 'unknown'}", str(exc)
    finally:
        sock.close()


def interface_line(ip_cmd: str) -> str:
    result = run_host([ip_cmd, "-o", "-4", "addr", "show"], timeout=3)
    for line in result.stdout.splitlines():
        if HOST_CIDR in line.split():
            return line
    return ""


def sample(ip_cmd: str, lsusb_cmd: str, ping_cmd: str, elapsed: float) -> dict[str, object]:
    usb = run_host([lsusb_cmd, "-d", USB_ID], timeout=3)
    line = interface_line(ip_cmd)
    ping = run_host([ping_cmd, "-c", "1", "-W", "1", PHONE_IP], timeout=2.5)
    state, banner = tcp_state(PHONE_IP)
    return {
        "elapsed_seconds": round(elapsed, 3),
        "time": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "usb_enumeration": usb.returncode == 0 and bool(usb.stdout.strip()),
        "usb_line": usb.stdout.strip(),
        "host_usb_network_interface": bool(line),
        "interface_line": line,
        "ping_172_16_42_1": ping.returncode == 0,
        "tcp22_state": state,
        "ssh_banner_text": banner,
        "ssh_banner": state == "ssh-banner",
    }


def host_snapshot(ip_cmd: str, lsusb_cmd: str) -> str:
    blocks: list[str] = []
    for name, args in (
        ("lsusb", [lsusb_cmd]),
        ("addresses", [ip_cmd, "-br", "addr"]),
        ("routes", [ip_cmd, "route"]),
        ("neighbors", [ip_cmd, "neigh"]),
    ):
        result = run_host(args)
        blocks.append(f"=== {name} ===\n{result.stdout}{result.stderr}")
    return "\n".join(blocks)


def local_preflight(root: Path, repo: Path) -> dict[str, object]:
    actual = git_blob(repo, FLASH_PATH)
    if actual != EXPECTED_FLASH_BLOB:
        raise U0nObserveError(
            f"checked-in U0n flash path changed: actual={actual!r} expected={EXPECTED_FLASH_BLOB!r}"
        )
    local = flash.local_evidence(root, repo)
    report_path = root / "build/a33-u0n-real-boot-sshd-trace-flash.txt"
    if not report_path.is_file():
        raise U0nObserveError(f"missing U0n flash report: {report_path}")
    report = common.kv(report_path)
    common.require(
        report,
        {
            "operation": "flash-exact-u0n-real-boot-sshd-trace",
            "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
            "recovery_previous_sha256": common.KNOWN_TWRP_SHA256,
            "recovery_partition_sha256": flash.EXPECTED_CANDIDATE_SHA256,
            "rootfs_validation": "identity-critical-hashes-and-exact-host-keys-passed",
            "userdata_written": "no",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "yes",
            "reboot_performed": "no",
            "flash_status": "passed",
        },
        "U0n flash report",
    )
    if common.sha_file(Path(local["manifest_path"])) != report.get("manifest_sha256"):
        raise U0nObserveError("U0n manifest changed after flashing")
    local["flash_report_path"] = report_path
    return local


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe exact U0n real-boot SSH trace for a full 90 seconds"
    )
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb
    ip_cmd = require_command("ip")
    lsusb_cmd = require_command("lsusb")
    ping_cmd = require_command("ping")

    local = local_preflight(root, repo)
    print("u0n_observer_local_preflight=passed")
    serial = common.select_recovery(adb, 30)
    flash.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    flash.validate_phone_rootfs(adb, serial, local)
    recovery_state = flash.recovery_helper.prepare(
        common, adb, serial, flash.EXPECTED_CANDIDATE_SHA256
    )
    try:
        print("u0n_recovery_partition_readback=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0nObserveError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    if args.preflight_only:
        print("u0n_observer_preflight_status=passed")
        print("phone_partition_writes=no")
        print("phone_reboot_performed=no")
        return 0

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0n-real-boot-sshd-trace-observation-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "host-before.txt").write_text(
        host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    (out / "preboot.json").write_text(
        json.dumps(
            {
                "adb_serial": serial,
                "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
                "observation_seconds": OBSERVATION_SECONDS,
                "rootfs_validation": "passed",
                "exact_host_keys": "passed",
                "phone_partition_writes": "no",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    started_wall = datetime.now().astimezone()
    started_mono = time.monotonic()
    common.run([adb, "-s", serial, "reboot", "recovery"])

    first: dict[str, float | None] = {
        "usb": None,
        "interface": None,
        "ping": None,
        "ssh_banner": None,
        "connection_refused": None,
    }
    states: dict[str, int] = {}
    with (out / "observation.jsonl").open("w", encoding="utf-8") as stream:
        while True:
            elapsed = time.monotonic() - started_mono
            row = sample(ip_cmd, lsusb_cmd, ping_cmd, elapsed)
            if row["usb_enumeration"] and first["usb"] is None:
                first["usb"] = elapsed
            if row["host_usb_network_interface"] and first["interface"] is None:
                first["interface"] = elapsed
            if row["ping_172_16_42_1"] and first["ping"] is None:
                first["ping"] = elapsed
            if row["ssh_banner"] and first["ssh_banner"] is None:
                first["ssh_banner"] = elapsed
            if row["tcp22_state"] == "connection-refused" and first["connection_refused"] is None:
                first["connection_refused"] = elapsed
            state = str(row["tcp22_state"])
            states[state] = states.get(state, 0) + 1
            stream.write(json.dumps(row, sort_keys=True) + "\n")
            stream.flush()
            if elapsed >= OBSERVATION_SECONDS:
                break
            time.sleep(0.5)

    finished_wall = datetime.now().astimezone()
    (out / "host-after.txt").write_text(
        host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    journalctl = shutil.which("journalctl")
    if journalctl:
        journal = run_host(
            [
                journalctl,
                "-k",
                "--since",
                started_wall.isoformat(timespec="seconds"),
                "--until",
                finished_wall.isoformat(timespec="seconds"),
            ],
            timeout=20,
        )
        (out / "host-kernel-journal.txt").write_text(
            journal.stdout + journal.stderr, encoding="utf-8"
        )

    summary = {
        "created": finished_wall.isoformat(timespec="microseconds"),
        "operation": "observe-u0n-real-boot-sshd-trace-90s",
        "implementation_language": "python3",
        "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
        "observation_seconds": OBSERVATION_SECONDS,
        "first_usb_seconds": first["usb"],
        "first_interface_seconds": first["interface"],
        "first_ping_seconds": first["ping"],
        "first_connection_refused_seconds": first["connection_refused"],
        "first_ssh_banner_seconds": first["ssh_banner"],
        "tcp22_state_counts": states,
        "ssh_banner_ever": first["ssh_banner"] is not None,
        "ping_ever": first["ping"] is not None,
        "phone_partition_writes": "no",
        "phone_reboot_performed": "yes-recovery-target-only",
        "observation_status": "passed-full-90-second-window",
        "next_action": "enter-download-mode-and-restore-exact-twrp-immediately",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = common.sha_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"observation_directory={out}")
    print(f"observation_archive={archive}")
    print(f"observation_archive_sha256={archive_sha}")
    print("NEXT: enter Samsung Download Mode now, then run:")
    print("python3 scripts/restore-a33-twrp-odin.py RESTORE-EXACT-TWRP")
    print("After Odin, boot TWRP directly; do not boot Android or U0n again.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0nObserveError,
        flash.U0nFlashError,
        flash.restore.RestoreError,
        flash.restore.cleanup.CleanupV2Error,
        flash.recovery_helper.ExactRecoveryNodeError,
        flash.rescue.RescueError,
        common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(f"REFUSING U0n OBSERVER: {exc}", file=sys.stderr)
        raise SystemExit(1)

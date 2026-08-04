from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tarfile
import time
from typing import Callable

PHONE_IP = "172.16.42.1"
HOST_CIDR = "172.16.42.2/24"
USB_ID = "04e8:6860"


@dataclass(frozen=True)
class ObserverProfile:
    expected_flash_operation: str
    flash_report_name: str
    output_prefix: str
    observation_operation: str

    def validate(self) -> None:
        token = r"[a-z0-9][a-z0-9._-]*"
        if not re.fullmatch(token, self.expected_flash_operation):
            raise ValueError("unsafe expected flash operation")
        if not re.fullmatch(token + r"\.txt", self.flash_report_name):
            raise ValueError("unsafe flash report name")
        if not re.fullmatch(token, self.output_prefix):
            raise ValueError("unsafe observation output prefix")
        if not re.fullmatch(token, self.observation_operation):
            raise ValueError("unsafe observation operation")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def run_host(args: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
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
        return subprocess.CompletedProcess(
            args=args,
            returncode=124,
            stdout=stdout,
            stderr=stderr + f"\ncommand_timeout_seconds={timeout}\n",
        )


def require_command(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"missing required host command: {name}")
    return path


def parse_interface_for_cidr(output: str, cidr: str) -> str | None:
    for raw in output.splitlines():
        if cidr in raw.split():
            return raw
    return None


def valid_ssh_banner(data: bytes) -> bool:
    return data.startswith(b"SSH-")


def read_ssh_banner(host: str, port: int = 22, timeout: float = 0.75) -> bytes:
    try:
        with socket.create_connection((host, port), timeout=timeout) as connection:
            connection.settimeout(timeout)
            return connection.recv(128)
    except OSError:
        return b""


def validate_flash_report(
    common,
    validate_local: Callable[[Path, Path], dict[str, object]],
    profile: ObserverProfile,
    root: Path,
    repo: Path,
) -> dict[str, object]:
    try:
        profile.validate()
    except ValueError as exc:
        common.refuse(str(exc))

    local = validate_local(root, repo)
    manifest_path = Path(local["manifest_path"])
    report_path = root / "build" / profile.flash_report_name
    if not report_path.is_file():
        common.refuse(f"missing flash report: {report_path}")
    report = common.kv(report_path)
    expected_sha = str(local["candidate_sha"])
    common.require(
        report,
        {
            "operation": profile.expected_flash_operation,
            "implementation_language": "python3",
            "userdata_validation": "identity-and-critical-content-passed",
            "candidate_sha256": expected_sha,
            "recovery_partition_sha256": expected_sha,
            "userdata_written": "no",
            "cache_written": "no",
            "super_written": "no",
            "boot_written": "no",
            "recovery_written": "yes",
            "reboot_performed": "no",
            "flash_status": "passed",
        },
        "rootfs flash report",
    )
    report_manifest = Path(report.get("candidate_manifest", ""))
    if report_manifest.resolve() != manifest_path.resolve():
        common.refuse("flash report references a different manifest")
    if common.sha_file(manifest_path) != report.get("candidate_manifest_sha256"):
        common.refuse("candidate manifest changed after flashing")
    deploy_path = Path(report.get("deployment_report", ""))
    if not deploy_path.is_file() or common.sha_file(deploy_path) != report.get(
        "deployment_report_sha256"
    ):
        common.refuse("deployment report changed after flashing")
    local.update(
        {
            "flash_report_path": report_path,
            "flash_report": report,
            "expected_recovery_sha": expected_sha,
        }
    )
    return local


def validate_preboot(common, adb: str, serial: str, local: dict[str, object]) -> dict[str, object]:
    values, sections = common.live_state(adb, serial)
    expected = {
        "recovery_sha": str(local["expected_recovery_sha"]),
        "userdata_resolved": common.EXPECTED_USERDATA,
        "userdata_bytes": str(common.EXPECTED_USERDATA_BYTES),
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for section in ("mount_users", "swap_users", "dm_users"):
        if sections.get(section):
            mismatches.append(f"{section}: active={sections[section]!r}")
    root_uuid, root_label = common.ext4_identity(adb, serial)
    if root_uuid != local["root_uuid"]:
        mismatches.append(f"root_uuid: actual={root_uuid!r} expected={local['root_uuid']!r}")
    if root_label != "pmOS_root":
        mismatches.append(f"root_label: actual={root_label!r} expected='pmOS_root'")
    if mismatches:
        common.refuse("preboot TWRP/rootfs state is unsafe:\n" + "\n".join(mismatches))
    return {
        **values,
        "root_uuid": root_uuid,
        "root_label": root_label,
        "mount_users": sections.get("mount_users", []),
        "swap_users": sections.get("swap_users", []),
        "dm_users": sections.get("dm_users", []),
    }


def host_snapshot(ip_cmd: str, lsusb_cmd: str) -> str:
    blocks: list[str] = []
    for title, args in (
        ("lsusb", [lsusb_cmd]),
        ("addresses", [ip_cmd, "-br", "addr"]),
        ("routes", [ip_cmd, "route"]),
        ("neighbors", [ip_cmd, "neigh"]),
    ):
        completed = run_host(args, timeout=5)
        blocks.append(f"=== {title} ===\n{completed.stdout}{completed.stderr}")
    return "\n".join(blocks)


def observation_sample(
    ip_cmd: str,
    lsusb_cmd: str,
    ping_cmd: str,
    elapsed: float,
) -> dict[str, object]:
    usb = run_host([lsusb_cmd, "-d", USB_ID], timeout=3)
    usb_present = usb.returncode == 0 and bool(usb.stdout.strip())
    addresses = run_host([ip_cmd, "-o", "-4", "addr", "show"], timeout=3)
    interface_line = parse_interface_for_cidr(addresses.stdout, HOST_CIDR)
    interface_present = interface_line is not None
    ping = run_host([ping_cmd, "-c", "1", "-W", "1", PHONE_IP], timeout=2.5)
    ping_ok = ping.returncode == 0
    banner = read_ssh_banner(PHONE_IP)
    ssh_ok = valid_ssh_banner(banner)
    return {
        "elapsed_seconds": round(elapsed, 3),
        "time": now_iso(),
        "usb_enumeration": usb_present,
        "usb_line": usb.stdout.strip(),
        "host_usb_network_interface": interface_present,
        "interface_line": interface_line or "",
        "ping_172_16_42_1": ping_ok,
        "ssh_banner": ssh_ok,
        "ssh_banner_text": banner.decode("ascii", "replace").strip(),
        "simultaneous_success": usb_present and interface_present and ping_ok and ssh_ok,
    }


def write_key_values(path: Path, values: list[tuple[str, object]]) -> None:
    path.write_text(
        "\n".join(f"{key}={value}" for key, value in values) + "\n",
        encoding="utf-8",
    )


def execute_observer(
    common,
    validate_local: Callable[[Path, Path], dict[str, object]],
    profile: ObserverProfile,
    *,
    root: Path,
    repo: Path,
    adb_argument: str,
    max_seconds: int,
    preflight_only: bool,
) -> int:
    if not 1 <= max_seconds <= 900:
        common.refuse("max_seconds must be between 1 and 900")

    local = validate_flash_report(common, validate_local, profile, root, repo)
    print("observer_local_preflight=passed")
    print(f"expected_recovery_sha256={local['expected_recovery_sha']}")
    print("phone_partition_writes=no")
    if preflight_only:
        return 0

    adb = shutil.which(adb_argument) or adb_argument
    ip_cmd = require_command("ip")
    lsusb_cmd = require_command("lsusb")
    ping_cmd = require_command("ping")
    serial = common.select_recovery(adb, 30)
    preboot = validate_preboot(common, adb, serial, local)
    print(f"adb_serial={serial}")
    print("preboot_state=passed")

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"{profile.output_prefix}-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    archive = Path(str(out) + ".tar.gz")

    write_key_values(
        out / "manifest.txt",
        [
            ("created", now_iso()),
            ("operation", profile.observation_operation),
            ("implementation_language", "python3"),
            ("phone_ip", PHONE_IP),
            ("host_cidr", HOST_CIDR),
            ("max_seconds", max_seconds),
            ("flash_report", local["flash_report_path"]),
            ("flash_report_sha256", common.sha_file(Path(local["flash_report_path"]))),
            ("expected_recovery_sha256", local["expected_recovery_sha"]),
            ("phone_partition_writes", "no"),
            ("reboot_target", "recovery"),
        ],
    )
    (out / "preboot-state.json").write_text(
        json.dumps(preboot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out / "host-baseline.txt").write_text(
        host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )

    start_wall = datetime.now().astimezone()
    start_mono = time.monotonic()
    common.run([adb, "-s", serial, "reboot", "recovery"])

    ever = {
        "usb_enumeration": False,
        "host_usb_network_interface": False,
        "ping_172_16_42_1": False,
        "ssh_banner": False,
    }
    success_elapsed: float | None = None
    with (out / "observation.jsonl").open("w", encoding="utf-8") as log:
        while True:
            elapsed = time.monotonic() - start_mono
            sample = observation_sample(ip_cmd, lsusb_cmd, ping_cmd, elapsed)
            for key in ever:
                ever[key] = ever[key] or bool(sample[key])
            log.write(json.dumps(sample, sort_keys=True) + "\n")
            log.flush()
            if sample["simultaneous_success"]:
                success_elapsed = elapsed
                break
            if elapsed >= max_seconds:
                break
            time.sleep(0.5)

    end_wall = datetime.now().astimezone()
    (out / "host-final.txt").write_text(
        host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    journalctl = shutil.which("journalctl")
    if journalctl:
        journal = run_host(
            [
                journalctl,
                "-k",
                "--since",
                start_wall.isoformat(timespec="seconds"),
                "--until",
                end_wall.isoformat(timespec="seconds"),
            ],
            timeout=20,
        )
        (out / "host-kernel-journal.txt").write_text(
            journal.stdout + journal.stderr,
            encoding="utf-8",
        )

    status = (
        "passed-rootfs-network-and-ssh-ready"
        if success_elapsed is not None
        else "failed-no-simultaneous-rootfs-network-and-ssh"
    )
    summary = [
        ("started", start_wall.isoformat(timespec="microseconds")),
        ("finished", end_wall.isoformat(timespec="microseconds")),
        ("elapsed_seconds", round((end_wall - start_wall).total_seconds(), 3)),
        ("success_second", "none" if success_elapsed is None else round(success_elapsed, 3)),
        ("usb_enumeration_ever", "yes" if ever["usb_enumeration"] else "no"),
        (
            "host_usb_network_interface_ever",
            "yes" if ever["host_usb_network_interface"] else "no",
        ),
        ("ping_172_16_42_1_ever", "yes" if ever["ping_172_16_42_1"] else "no"),
        ("ssh_banner_ever", "yes" if ever["ssh_banner"] else "no"),
        ("simultaneous_success", "yes" if success_elapsed is not None else "no"),
        ("phone_partition_writes", "no"),
        ("observation_status", status),
    ]
    write_key_values(out / "summary.txt", summary)

    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = common.sha_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )

    for key, value in summary:
        print(f"{key}={value}")
    print(f"observation_directory={out}")
    print(f"observation_archive={archive}")
    print(f"observation_archive_sha256={archive_sha}")
    return 0 if success_elapsed is not None else 3

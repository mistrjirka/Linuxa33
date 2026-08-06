#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
HELPERS_PATH = HERE / "observe-a33-u0p-corrected-sshd-source-hash.py"
EXPECTED_HELPERS_BLOB = "ab35fa03ae34a48bf1e902eb3b7d91dac951c011"
SERIAL = "RFCTA00V43L"
FLASH_REPORT = Path.home() / "a33-port/build/a33-u0r-early-metadata-trace-flash.txt"
RESULT_ROOT = Path.home() / "a33-port/build/runtime-results"
OBSERVATION_SECONDS = 90
TWRP_REBOOT = "/system/bin/twrp"


class MinimalObserveError(RuntimeError):
    pass


def load_helpers():
    spec = importlib.util.spec_from_file_location("a33_u0r_minimal_helpers", HELPERS_PATH)
    if spec is None or spec.loader is None:
        raise MinimalObserveError(f"cannot load observer helpers: {HELPERS_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def git_blob(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(HERE.parent), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
        check=True,
    )
    return completed.stdout.strip()


def read_kv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


def adb_run(adb: str, args: list[str], timeout: float, check: bool = True) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [adb, "-s", SERIAL, *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if check and completed.returncode != 0:
        raise MinimalObserveError(
            f"ADB command failed rc={completed.returncode}: {args!r}\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )
    return completed


def shell(adb: str, script: str, timeout: float = 10) -> str:
    completed = subprocess.run(
        [adb, "-s", SERIAL, "shell", "sh", "-s"],
        input=script,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise MinimalObserveError(
            f"TWRP shell failed rc={completed.returncode}\n"
            f"stdout={completed.stdout[-2000:]}\nstderr={completed.stderr[-2000:]}"
        )
    return completed.stdout.replace("\r\n", "\n").replace("\r", "")


def direct_probe(adb: str) -> None:
    subprocess.run(
        [adb, "start-server"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
        check=False,
    )
    for attempt in range(1, 16):
        try:
            output = shell(adb, "printf 'u0r_twrp_ready\\n'\n", timeout=3)
        except (MinimalObserveError, subprocess.TimeoutExpired):
            output = ""
        if output.strip() == "u0r_twrp_ready":
            print(f"twrp_adb_ready=passed attempt={attempt}", flush=True)
            return
        time.sleep(1)
    raise MinimalObserveError("exact-serial TWRP shell did not answer")


def main() -> int:
    helpers_blob = git_blob(HELPERS_PATH)
    if helpers_blob != EXPECTED_HELPERS_BLOB:
        raise MinimalObserveError(
            f"observer helper changed: actual={helpers_blob} expected={EXPECTED_HELPERS_BLOB}"
        )
    helpers_module = load_helpers()
    helpers = helpers_module.helpers

    if not FLASH_REPORT.is_file():
        raise MinimalObserveError(f"missing successful U0r flash report: {FLASH_REPORT}")
    report = read_kv(FLASH_REPORT)
    if report.get("flash_status") != "passed":
        raise MinimalObserveError("U0r flash report is not successful")
    if report.get("recovery_written") != "yes" or report.get("reboot_performed") != "no":
        raise MinimalObserveError("U0r flash report does not describe installed-not-rebooted state")
    candidate_sha = report.get("candidate_sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", candidate_sha):
        raise MinimalObserveError("U0r flash report has invalid candidate SHA256")

    adb = shutil.which("adb") or "adb"
    ip_cmd = helpers.require_command("ip")
    lsusb_cmd = helpers.require_command("lsusb")
    ping_cmd = helpers.require_command("ping")

    print("stage=probing-current-twrp", flush=True)
    direct_probe(adb)

    print("stage=verifying-installed-u0r-hash", flush=True)
    state = shell(
        adb,
        "set -eu\n"
        "[ -x /system/bin/twrp ]\n"
        "sha256sum /dev/block/by-name/recovery\n"
        "cat /proc/sys/kernel/random/boot_id\n"
        "pidof adbd 2>/dev/null || true\n",
        timeout=30,
    ).splitlines()
    if len(state) < 2:
        raise MinimalObserveError(f"incomplete TWRP state: {state!r}")
    installed_sha = state[0].split()[0]
    if installed_sha != candidate_sha:
        raise MinimalObserveError(
            f"installed recovery differs from flash report: installed={installed_sha} expected={candidate_sha}"
        )
    old_boot_id = state[1].strip()
    old_adbd_pid = state[2].strip() if len(state) > 2 else ""
    old_usb_line = helpers.require_single_usb_line(lsusb_cmd)
    print("installed_u0r_recovery_hash=passed", flush=True)
    print(f"candidate_sha256={candidate_sha}", flush=True)
    print(f"preboot_boot_id={old_boot_id}", flush=True)

    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = RESULT_ROOT / f"u0r-minimal-observation-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "host-before.txt").write_text(
        helpers.u0n_observer.host_snapshot(ip_cmd, lsusb_cmd), encoding="utf-8"
    )
    (out / "preboot.json").write_text(
        json.dumps(
            {
                "candidate_sha256": candidate_sha,
                "boot_id": old_boot_id,
                "adbd_pid": old_adbd_pid,
                "usb_line": old_usb_line,
                "twrp_command": f"{TWRP_REBOOT} reboot recovery",
            },
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )

    print("stage=rebooting-u0r", flush=True)
    command_start_wall = datetime.now().astimezone()
    command_start = time.monotonic()
    reboot = adb_run(adb, ["shell", TWRP_REBOOT, "reboot", "recovery"], timeout=15, check=False)
    (out / "twrp-reboot-command.txt").write_text(
        f"returncode={reboot.returncode}\nstdout_begin\n{reboot.stdout}stdout_end\n"
        f"stderr_begin\n{reboot.stderr}stderr_end\n",
        encoding="utf-8",
    )

    transition_elapsed, transition_rows = helpers.wait_for_transition(
        adb,
        SERIAL,
        lsusb_cmd,
        old_boot_id,
        old_usb_line,
        out / "transition.jsonl",
        command_start,
    )
    if transition_elapsed is None:
        raise MinimalObserveError("old TWRP ADB/USB instance never provably disappeared")
    print(f"reboot_transition_verified_seconds={transition_elapsed:.3f}", flush=True)

    observation_start = time.monotonic()
    rows: list[dict[str, object]] = []
    first_usb = first_interface = first_ping = first_refused = first_banner = None
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
            state_name = str(row["tcp22_state"])
            tcp_counts[state_name] = tcp_counts.get(state_name, 0) + 1
            if state_name == "connection-refused" and first_refused is None:
                first_refused = elapsed
            if state_name == "ssh-banner" and first_banner is None:
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
        "operation": "observe-u0r-minimal-transition-proven-90s",
        "candidate_sha256": candidate_sha,
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
        "observation_status": "passed",
        "next_action": "restore-exact-twrp-then-collect-current-persistent-state-v2",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive, archive_sha = helpers.write_archive(out)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)
    print(f"observation_archive={archive}", flush=True)
    print(f"observation_archive_sha256={archive_sha}", flush=True)
    print("NEXT: enter Download Mode and restore exact TWRP.", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("U0r MINIMAL OBSERVER INTERRUPTED", file=sys.stderr)
        raise SystemExit(130)
    except (
        MinimalObserveError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"U0r MINIMAL OBSERVER FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

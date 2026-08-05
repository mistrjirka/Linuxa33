#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
OBSERVER_PATH = HERE / "observe-a33-u0n-real-boot-sshd-trace.py"
TWRP_RESTORE_PATH = HERE / "restore-a33-twrp-odin.py"
EXPECTED_OBSERVER_BLOB = "31b6f288ddeb743afb8b338b08c7169dbfe4f31e"
EXPECTED_TWRP_RESTORE_BLOB = "70985f243bd3462cbad97c05ad379eda2958e5c7"
RAMDISK_SIZE_HEX = "0x00aa0cff"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


observer = load("a33_u0n_collector_observer", OBSERVER_PATH)
twrp_restore = load("a33_u0n_collector_twrp", TWRP_RESTORE_PATH)
common = observer.common
flash = observer.flash


class U0nCollectError(RuntimeError):
    pass


FOCUS_PATTERN = re.compile(
    r"a33x-u0n-real-boot-sshd|a33x-u0m-watchdog-handoff|a33x-u0l-openrc-cgroup-isolation|"
    r"a33x-u0k-direct-mount|a33x-watchdog-v2|sshd(?:\.pam)?|ssh-keygen|"
    r"openrc|start-stop-daemon|nft|dport[[:space:]]+22|watchdog0|watchdog reset|"
    r"cl0_wdtreset|freqboost|\bems\b|cgroup(?:\.procs)?|switch_root|sysroot|"
    r"kernel panic|panic - not syncing|call trace|bug:|oops|unable to handle|"
    r"exynos_plist_add|exynos_pm_qos|exynos_ufs_probe|ext4-fs|dwc3|gadget",
    re.IGNORECASE,
)

COUNT_PATTERNS = {
    "u0n_setup_begin_count": r"a33x-u0n-real-boot-sshd: stage=setup-begin",
    "u0n_setup_success_count": r"a33x-u0n-real-boot-sshd: stage=setup-success",
    "u0n_switch_root_ready_count": r"a33x-u0n-real-boot-sshd: stage=switch-root-ready",
    "u0n_error_count": r"a33x-u0n-real-boot-sshd: error=",
    "u0n_script_loaded_count": r"a33x-u0n-real-boot-sshd: event=script-loaded",
    "u0n_update_command_count": r"a33x-u0n-real-boot-sshd: event=update-command",
    "u0n_checkconfig_enter_count": r"a33x-u0n-real-boot-sshd: event=checkconfig-enter",
    "u0n_checkconfig_exit_count": r"a33x-u0n-real-boot-sshd: event=checkconfig-exit",
    "u0n_start_pre_enter_count": r"a33x-u0n-real-boot-sshd: event=start-pre-enter",
    "u0n_start_pre_exit_count": r"a33x-u0n-real-boot-sshd: event=start-pre-exit",
    "u0n_start_post_enter_count": r"a33x-u0n-real-boot-sshd: event=start_post-enter",
    "u0n_start_post_exit_count": r"a33x-u0n-real-boot-sshd: event=start_post-exit",
    "u0n_stop_pre_enter_count": r"a33x-u0n-real-boot-sshd: event=stop_pre-enter",
    "u0n_stop_pre_exit_count": r"a33x-u0n-real-boot-sshd: event=stop_pre-exit",
    "u0n_stop_post_enter_count": r"a33x-u0n-real-boot-sshd: event=stop_post-enter",
    "u0n_stop_post_exit_count": r"a33x-u0n-real-boot-sshd: event=stop_post-exit",
    "u0n_monitor_started_count": r"a33x-u0n-real-boot-sshd: event=monitor-started",
    "u0n_monitor_complete_count": r"a33x-u0n-real-boot-sshd: event=monitor-complete",
    "u0n_snapshot_count": r"a33x-u0n-real-boot-sshd: event=snapshot",
    "u0n_nft_count": r"a33x-u0n-real-boot-sshd: event=nft",
    "u0n_listener_yes_count": r"a33x-u0n-real-boot-sshd: event=snapshot[^\n]*listener=yes",
    "u0n_alive_yes_count": r"a33x-u0n-real-boot-sshd: event=snapshot[^\n]*alive=yes",
    "u0n_openrc_started_count": r"a33x-u0n-real-boot-sshd: event=snapshot[^\n]*openrc=[^\n]*started",
    "u0m_shutdown_success_count": r"a33x-u0m-watchdog-handoff: stage=shutdown-success",
    "u0m_shutdown_error_count": r"a33x-u0m-watchdog-handoff: error=",
    "u0l_mask_success_count": r"a33x-u0l-openrc-cgroup-isolation: stage=mask-success",
    "watchdog_did_not_stop_count": r"watchdog0: watchdog did not stop",
    "watchdog_reset_count": r"watchdog reset|cl0_wdtreset",
    "ems_freqboost_count": r"freqboost|\bems\b",
    "kernel_panic_count": r"kernel panic|panic - not syncing",
    "ufs_pm_qos_panic_count": r"exynos_plist_add|exynos_pm_qos|exynos_ufs_probe",
}


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def sanitize(data: bytes) -> str:
    result: list[str] = []
    for byte in data:
        if byte in (9, 10, 13) or 32 <= byte < 127:
            result.append(chr(byte))
        elif byte == 0:
            result.append("\n")
        else:
            result.append("\ufffd")
    return "".join(result)


def focused_lines(text: str) -> list[str]:
    return [
        f"{number}:{line}"
        for number, line in enumerate(text.splitlines(), 1)
        if FOCUS_PATTERN.search(line)
    ]


def counts(text: str) -> dict[str, int]:
    return {
        key: len(re.findall(pattern, text, flags=re.IGNORECASE))
        for key, pattern in COUNT_PATTERNS.items()
    }


def latest_observation(root: Path) -> Path:
    candidates = [
        path
        for path in (root / "build/runtime-results").glob(
            "u0n-real-boot-sshd-trace-observation-*"
        )
        if path.is_dir()
    ]
    if not candidates:
        raise U0nCollectError("no U0n observation directory exists")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def capture(
    args: list[str], destination: Path, *, binary: bool = False, required: bool = True
) -> bytes:
    completed = common.run(args, text=not binary, check=False, timeout=30)
    if binary:
        assert isinstance(completed.stdout, bytes)
        assert isinstance(completed.stderr, bytes)
        payload = completed.stdout
        destination.write_bytes(payload)
        stderr = completed.stderr.decode(errors="replace")
    else:
        assert isinstance(completed.stdout, str)
        assert isinstance(completed.stderr, str)
        payload = completed.stdout.encode()
        destination.write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
        stderr = completed.stderr
    if required and (completed.returncode != 0 or not payload):
        raise U0nCollectError(
            f"required capture failed rc={completed.returncode}: {args!r}: {stderr.strip()}"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect focused U0n real-boot SSH trace after exact TWRP restore"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    for path, expected in (
        (OBSERVER_PATH, EXPECTED_OBSERVER_BLOB),
        (TWRP_RESTORE_PATH, EXPECTED_TWRP_RESTORE_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0nCollectError(
                f"checked-in collector dependency changed: {path.name} actual={actual!r} expected={expected!r}"
            )

    local = observer.local_preflight(root, repo)
    observation = latest_observation(root)
    observation_summary = observation / "summary.json"
    if not observation_summary.is_file():
        raise U0nCollectError("latest U0n observation lacks summary.json")
    summary_data = json.loads(observation_summary.read_text(encoding="utf-8"))
    if summary_data.get("observation_status") != "passed-full-90-second-window":
        raise U0nCollectError("latest U0n observation did not complete the full window")
    if summary_data.get("candidate_sha256") != flash.EXPECTED_CANDIDATE_SHA256:
        raise U0nCollectError("latest observation references another candidate")

    serial = common.select_recovery(adb, 30)
    fingerprint = flash.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    recovery_state = flash.recovery_helper.prepare(
        common, adb, serial, common.KNOWN_TWRP_SHA256
    )
    try:
        print("exact_twrp_recovery_partition=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0nCollectError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    result_root = root / "build/runtime-results"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0n-real-boot-sshd-trace-result-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)

    raw = capture(
        [adb, "-s", serial, "exec-out", "cat", "/proc/last_kmsg"],
        out / "last_kmsg.bin",
        binary=True,
        required=True,
    )
    text = sanitize(raw)
    (out / "last_kmsg.sanitized.txt").write_text(text, encoding="utf-8")
    focused = focused_lines(text)
    (out / "focused-last-kmsg.txt").write_text(
        "\n".join(focused) + "\n", encoding="utf-8"
    )

    for name, remote, required in (
        ("twrp-dmesg.txt", ["dmesg"], True),
        ("twrp-getprop.txt", ["getprop"], False),
        ("twrp-cmdline.txt", ["cat", "/proc/cmdline"], False),
        ("twrp-kernel.txt", ["sh", "-c", "uname -a; cat /proc/version"], False),
        (
            "log-source-state.txt",
            [
                "sh",
                "-c",
                "ls -la /proc/last_kmsg /sys/fs/pstore 2>&1; find /sys/fs/pstore -maxdepth 1 -type f -print 2>/dev/null",
            ],
            False,
        ),
    ):
        capture(
            [adb, "-s", serial, "shell", *remote],
            out / name,
            binary=False,
            required=required,
        )

    evidence = out / "host-evidence"
    evidence.mkdir()
    for source in (
        Path(local["flash_report_path"]),
        Path(local["manifest_path"]),
        Path(local["patch_path"]),
        Path(local["audit_path"]),
        root / "build/a33-twrp-odin-restore.txt",
    ):
        if source.is_file():
            shutil.copy2(source, evidence / source.name)
    shutil.copytree(observation, out / "observation")

    count_values = counts(text)
    result = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "collect-u0n-real-boot-sshd-trace-previous-boot",
        "implementation_language": "python3",
        "adb_serial": serial,
        "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
        "u0n_ramdisk_size": 11144447,
        "u0n_ramdisk_size_hex": RAMDISK_SIZE_HEX,
        "last_kmsg_bytes": len(raw),
        "focused_line_count": len(focused),
        "counts": count_values,
        "twrp_kernel_release": fingerprint["kernel_release"],
        "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
        "recovery_sha256": common.KNOWN_TWRP_SHA256,
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "collection_status": "passed",
    }
    (out / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = common.sha_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    print(f"result_directory={out}")
    print(f"result_archive={archive}")
    print(f"result_archive_sha256={archive_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0nCollectError,
        observer.U0nObserveError,
        flash.U0nFlashError,
        flash.restore.cleanup.CleanupV2Error,
        flash.recovery_helper.ExactRecoveryNodeError,
        common.Refusal,
        json.JSONDecodeError,
        OSError,
        ValueError,
    ) as exc:
        print(f"U0n TRACE COLLECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

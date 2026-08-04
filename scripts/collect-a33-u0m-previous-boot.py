#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import importlib.util
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load(
    "a33_u0m_collector_common",
    HERE / "flash-a33-u0i-python-direct-root-v2.py",
)
u0m = load(
    "a33_u0m_collector_flash",
    HERE / "flash-a33-u0m-watchdog-magic-close.py",
)

sys.path.insert(0, str(HERE / "lib"))
from a33_rootfs_boot_observer import ObserverProfile, validate_flash_report

PROFILE = ObserverProfile(
    expected_flash_operation="flash-exact-u0m-watchdog-magic-close",
    flash_report_name="a33-first-rootfs-u0m-watchdog-magic-close-flash.txt",
    output_prefix="u0m-watchdog-magic-close-observation",
    observation_operation="observe-u0m-watchdog-magic-close",
)
OUTPUT_PREFIX = "u0m-watchdog-magic-close-result"
FOCUS_PATTERN = re.compile(
    r"a33x-u0[klm]-|a33x-watchdog-v2|watchdog0|watchdog reset|cl0_wdtreset|"
    r"nowayout|magic close|openrc|cgroup(?:\.procs)?|freqboost|\bems\b|"
    r"switch_root|sysroot|sshd|ssh-keygen|networkmanager|"
    r"kernel panic|panic - not syncing|call trace|bug:|oops|unable to handle|"
    r"exynos_plist_add|exynos_pm_qos|exynos_ufs_probe|ext4-fs|dwc3|gadget",
    re.IGNORECASE,
)
COUNT_PATTERNS = {
    "u0m_shutdown_request_count": r"a33x-u0m-watchdog-handoff: stage=shutdown-request",
    "u0m_shutdown_success_count": r"a33x-u0m-watchdog-handoff: stage=shutdown-success",
    "u0m_shutdown_error_count": r"a33x-u0m-watchdog-handoff: error=",
    "u0l_mask_success_count": r"a33x-u0l-openrc-cgroup-isolation: stage=mask-success",
    "watchdog_shutdown_requested_count": r"a33x-watchdog-v2: shutdown requested",
    "watchdog_magic_close_completed_count": r"a33x-watchdog-v2: magic close completed",
    "watchdog_stopped_count": r"a33x-watchdog-v2: watchdog stopped for rootfs handoff",
    "watchdog_did_not_stop_count": r"watchdog0: watchdog did not stop",
    "watchdog_reset_count": r"watchdog reset|cl0_wdtreset",
    "openrc_count": r"\bopenrc\b",
    "cgroup_procs_count": r"cgroup\.procs",
    "freqboost_count": r"freqboost",
    "ems_count": r"\bems\b",
    "sshd_count": r"\bsshd\b|ssh-keygen",
    "ufs_pm_qos_panic_count": r"exynos_plist_add|exynos_pm_qos|exynos_ufs_probe",
    "kernel_panic_count": r"kernel panic|panic - not syncing",
}


class CollectionError(RuntimeError):
    pass


def sanitize_last_kmsg(data: bytes) -> str:
    output: list[str] = []
    for byte in data:
        if byte in (9, 10, 13) or 32 <= byte < 127:
            output.append(chr(byte))
        elif byte == 0:
            output.append("\n")
        else:
            output.append("\ufffd")
    return "".join(output)


def focused_lines(text: str) -> list[str]:
    return [
        f"{number}:{line}"
        for number, line in enumerate(text.splitlines(), 1)
        if FOCUS_PATTERN.search(line)
    ]


def count_summary(text: str) -> dict[str, int]:
    return {
        key: len(re.findall(pattern, text, flags=re.IGNORECASE))
        for key, pattern in COUNT_PATTERNS.items()
    }


def capture_command(
    common_module,
    args: list[str],
    destination: Path,
    *,
    required: bool,
    binary: bool = False,
) -> bytes:
    completed = common_module.run(args, text=not binary, check=False, timeout=30)
    stdout = completed.stdout
    stderr = completed.stderr
    if binary:
        assert isinstance(stdout, bytes) and isinstance(stderr, bytes)
        payload = stdout
        destination.write_bytes(payload)
        diagnostic = stderr.decode("utf-8", errors="replace")
    else:
        assert isinstance(stdout, str) and isinstance(stderr, str)
        payload = stdout.encode()
        destination.write_text(stdout + stderr, encoding="utf-8")
        diagnostic = stderr
    if required and (completed.returncode != 0 or not payload):
        raise CollectionError(
            f"required capture failed rc={completed.returncode}: {args!r}: "
            f"{diagnostic.strip()}"
        )
    return payload


def latest_observation(result_root: Path) -> Path | None:
    candidates = [
        path
        for path in result_root.glob(f"{PROFILE.output_prefix}-*")
        if path.is_dir()
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def copy_if_file(source: Path, destination: Path) -> None:
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect U0m watchdog-handoff previous-boot evidence after exact "
            "TWRP has been restored and booted directly"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()

    local = validate_flash_report(common, u0m.validate_local, PROFILE, root, repo)
    adb = shutil.which(args.adb) or args.adb
    serial = common.select_recovery(adb, 30)
    recovery_sha_text = common.adb_shell(
        adb,
        serial,
        'sha256sum "$1"\n',
        common.RECOVERY,
    )
    recovery_fields = recovery_sha_text.split()
    recovery_sha = recovery_fields[0] if recovery_fields else ""
    if recovery_sha != common.KNOWN_TWRP_SHA256:
        raise CollectionError(
            "exact known-good TWRP is not running: "
            f"actual={recovery_sha!r} expected={common.KNOWN_TWRP_SHA256}"
        )

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"{OUTPUT_PREFIX}-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)

    raw = capture_command(
        common,
        [adb, "-s", serial, "exec-out", "cat", "/proc/last_kmsg"],
        out / "last_kmsg.bin",
        required=True,
        binary=True,
    )
    sanitized = sanitize_last_kmsg(raw)
    (out / "last_kmsg.sanitized.txt").write_text(sanitized, encoding="utf-8")
    (out / "focused-last-kmsg.txt").write_text(
        "\n".join(focused_lines(sanitized)) + "\n",
        encoding="utf-8",
    )

    for name, remote_args, required in (
        ("twrp-dmesg.txt", ["dmesg"], True),
        ("twrp-getprop.txt", ["getprop"], False),
        ("twrp-cmdline.txt", ["cat", "/proc/cmdline"], False),
        ("twrp-kernel.txt", ["sh", "-c", "uname -a; cat /proc/version"], False),
        (
            "watchdog-sysfs.txt",
            [
                "sh",
                "-c",
                "for f in /sys/class/watchdog/watchdog0/{identity,options,nowayout,state,status,timeleft,timeout}; do "
                "echo ===$f===; cat $f 2>&1 || true; done",
            ],
            False,
        ),
        (
            "log-source-state.txt",
            [
                "sh",
                "-c",
                "ls -la /proc/last_kmsg /sys/fs/pstore 2>&1; "
                "find /sys/fs/pstore -maxdepth 1 -type f -print 2>/dev/null",
            ],
            False,
        ),
    ):
        capture_command(
            common,
            [adb, "-s", serial, "shell", *remote_args],
            out / name,
            required=required,
        )
    (out / "recovery-sha256.txt").write_text(
        f"{recovery_sha}  {common.RECOVERY}\n", encoding="utf-8"
    )

    evidence_dir = out / "host-evidence"
    evidence_dir.mkdir()
    for source in (
        Path(local["flash_report_path"]),
        Path(local["manifest_path"]),
        Path(local["patch_path"]),
        Path(local["audit_path"]),
        root / "build/a33-twrp-odin-restore.txt",
        root / "build/a33-u0m-flash-preflight-audit-console.txt",
    ):
        copy_if_file(source, evidence_dir / source.name)
    observation = latest_observation(result_root)
    if observation is not None:
        shutil.copytree(observation, out / "observation")

    counts = count_summary(sanitized)
    summary_pairs: list[tuple[str, object]] = [
        ("created", datetime.now().astimezone().isoformat(timespec="microseconds")),
        ("operation", "collect-u0m-previous-boot-python"),
        ("implementation_language", "python3"),
        ("adb_serial", serial),
        ("last_kmsg_bytes", len(raw)),
        ("recovery_sha256", recovery_sha),
        ("recovery_status", "verified-known-good-twrp"),
        *[(key, value) for key, value in counts.items()],
        ("phone_partition_writes", "no"),
        ("collection_status", "passed"),
    ]
    summary = out / "summary.txt"
    summary.write_text(
        "".join(f"{key}={value}\n" for key, value in summary_pairs),
        encoding="utf-8",
    )

    archive = Path(str(out) + ".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = common.sha_file(archive)
    Path(str(archive) + ".sha256").write_text(
        f"{archive_sha}  {archive}\n", encoding="utf-8"
    )
    for key, value in summary_pairs:
        print(f"{key}={value}")
    print(f"result_directory={out}")
    print(f"result_archive={archive}")
    print(f"result_archive_sha256={archive_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CollectionError, common.Refusal, RuntimeError, OSError, ValueError) as exc:
        print(f"U0m PREVIOUS-BOOT COLLECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

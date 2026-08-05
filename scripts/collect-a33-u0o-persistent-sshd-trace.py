#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
FLASH_PATH = HERE / "flash-a33-u0o-persistent-sshd-trace.py"
OBSERVER_PATH = HERE / "observe-a33-u0o-persistent-sshd-trace.py"
TWRP_RESTORE_PATH = HERE / "restore-a33-twrp-odin.py"
EXPECTED_FLASH_BLOB = "441f3c055ca25aa06cd195f1f28b78365817949c"
EXPECTED_OBSERVER_BLOB = "952ce1d03b79f4cb4d29ad83600d2220be727e01"
EXPECTED_TWRP_RESTORE_BLOB = "70985f243bd3462cbad97c05ad379eda2958e5c7"
TRACE_PATH = "/var/log/a33x-u0o-real-boot-sshd.log"
MAX_TRACE_BYTES = 1048576


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_u0o_collector_flash", FLASH_PATH)
observer = load("a33_u0o_collector_observer", OBSERVER_PATH)
twrp_restore = load("a33_u0o_collector_twrp_restore", TWRP_RESTORE_PATH)
common = flash.common


class U0oCollectError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def values(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    active = False
    for line in text.replace("\r", "").splitlines():
        if line.endswith("_begin"):
            active = True
        elif line.endswith("_end"):
            active = False
        elif not active and "=" in line:
            key, value = line.split("=", 1)
            result.setdefault(key, value)
    return result


def section(text: str, name: str) -> list[str]:
    begin = f"{name}_begin"
    end = f"{name}_end"
    active = False
    result: list[str] = []
    for line in text.replace("\r", "").splitlines():
        if line == begin:
            active = True
            continue
        if line == end:
            active = False
            continue
        if active:
            result.append(line)
    return result


def latest_observation(root: Path) -> Path:
    candidates = [
        path
        for path in (root / "build/runtime-results").glob(
            "u0o-persistent-sshd-trace-observation-*"
        )
        if path.is_dir()
    ]
    if not candidates:
        raise U0oCollectError("no U0o observation directory exists")
    return max(candidates, key=lambda path: path.stat().st_mtime)


TRACE_READ_SCRIPT = rf'''set -eu
target="$1"
mountpoint=/tmp/a33x-u0o-trace-collect
trace="$mountpoint{TRACE_PATH}"
mounted=no
cleanup()
{{
    if [ "$mounted" = yes ]; then
        umount "$mountpoint" 2>/dev/null || true
    fi
    if ! awk -v point="$mountpoint" '$2 == point {{ found=1 }} END {{ exit found ? 0 : 1 }}' /proc/mounts; then
        rmdir "$mountpoint" 2>/dev/null || true
    fi
}}
trap cleanup EXIT
mkdir -p "$mountpoint"
umount "$mountpoint" 2>/dev/null || true
mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target" "$mountpoint"
mounted=yes
if [ ! -e "$trace" ]; then
    echo "trace_state=missing"
elif [ ! -f "$trace" ]; then
    echo "trace_state=present-not-regular"
else
    echo "trace_state=present-regular"
    echo "trace_mode=$(stat -c '%a' "$trace")"
    echo "trace_uid=$(stat -c '%u' "$trace")"
    echo "trace_gid=$(stat -c '%g' "$trace")"
    echo "trace_bytes=$(stat -c '%s' "$trace")"
    echo "trace_sha256=$(sha256sum "$trace" | awk 'NR==1 {{print $1}}')"
    echo "trace_base64_begin"
    base64 "$trace"
    echo "trace_base64_end"
fi
umount "$mountpoint"
mounted=no
echo "trace_readonly_unmount=passed"
echo "userdata_persistent_writes=no"
'''


COUNT_PATTERNS = {
    "candidate_trace_open_count": r"candidate=U0o-persistent-sshd-trace stage=trace-open",
    "initramfs_source_count": r"source=initramfs",
    "openrc_source_count": r"source=openrc",
    "setup_begin_count": r"stage=setup-begin",
    "setup_success_count": r"stage=setup-success",
    "switch_root_ready_count": r"stage=switch-root-ready",
    "script_loaded_count": r"event=script-loaded",
    "update_command_count": r"event=update-command",
    "checkconfig_enter_count": r"event=checkconfig-enter",
    "checkconfig_exit_count": r"event=checkconfig-exit",
    "start_pre_enter_count": r"event=start-pre-enter",
    "start_pre_exit_count": r"event=start-pre-exit",
    "start_post_enter_count": r"event=start_post-enter",
    "start_post_exit_count": r"event=start_post-exit",
    "stop_pre_enter_count": r"event=stop_pre-enter",
    "stop_pre_exit_count": r"event=stop_pre-exit",
    "stop_post_enter_count": r"event=stop_post-enter",
    "stop_post_exit_count": r"event=stop_post-exit",
    "monitor_started_count": r"event=monitor-started",
    "monitor_complete_count": r"event=monitor-complete",
    "snapshot_count": r"event=snapshot",
    "nft_count": r"event=nft",
    "listener_yes_count": r"event=snapshot[^\n]*listener=yes",
    "alive_yes_count": r"event=snapshot[^\n]*alive=yes",
    "openrc_started_count": r"event=snapshot[^\n]*openrc=[^\n]*started",
    "error_count": r"(?:^|\s)error=",
}


def trace_counts(text: str) -> dict[str, int]:
    return {
        key: len(re.findall(pattern, text, flags=re.IGNORECASE))
        for key, pattern in COUNT_PATTERNS.items()
    }


def capture_optional(
    args: list[str], destination: Path, *, binary: bool
) -> bytes:
    completed = common.run(args, text=not binary, check=False, timeout=30)
    if binary:
        assert isinstance(completed.stdout, bytes)
        assert isinstance(completed.stderr, bytes)
        payload = completed.stdout
        destination.write_bytes(payload)
        destination.with_suffix(destination.suffix + ".stderr.txt").write_text(
            completed.stderr.decode(errors="replace"), encoding="utf-8"
        )
    else:
        assert isinstance(completed.stdout, str)
        assert isinstance(completed.stderr, str)
        payload = completed.stdout.encode()
        destination.write_text(
            completed.stdout + completed.stderr, encoding="utf-8"
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Collect the one scoped U0o persistent SSH/OpenRC trace from userdata "
            "read-only after exact TWRP restoration"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    for path, expected in (
        (FLASH_PATH, EXPECTED_FLASH_BLOB),
        (OBSERVER_PATH, EXPECTED_OBSERVER_BLOB),
        (TWRP_RESTORE_PATH, EXPECTED_TWRP_RESTORE_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0oCollectError(
                f"checked-in U0o collector dependency changed: path={path.name} "
                f"actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    observation = latest_observation(root)
    observation_summary_path = observation / "summary.json"
    if not observation_summary_path.is_file():
        raise U0oCollectError("latest U0o observation lacks summary.json")
    observation_summary = json.loads(
        observation_summary_path.read_text(encoding="utf-8")
    )
    if observation_summary.get("candidate_sha256") != flash.EXPECTED_CANDIDATE_SHA256:
        raise U0oCollectError("latest observation references another candidate")
    if observation_summary.get("reboot_transition_verified") is not True:
        raise U0oCollectError("latest observation did not prove the old TWRP transition")
    if observation_summary.get("observation_status") != (
        "passed-transition-proven-full-90-second-window"
    ):
        raise U0oCollectError("latest observation did not complete the proven 90-second window")

    serial = common.select_recovery(adb, 30)
    fingerprint = flash.base.restore.cleanup.validate_runtime_fingerprint(adb, serial)
    recovery_state = flash.base.recovery_helper.prepare(
        common, adb, serial, common.KNOWN_TWRP_SHA256
    )
    try:
        print("exact_twrp_recovery_partition=passed")
        print(f"recovery_kernel_name={recovery_state.kernel_name}")
        print(f"recovery_kernel_dev={recovery_state.kernel_dev}")
    finally:
        cleanup_output = flash.base.recovery_helper.cleanup(
            common, adb, serial, recovery_state
        )
        if cleanup_output.count("exact_recovery_node_cleanup_status=passed") != 1:
            raise U0oCollectError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    # Validate restored rootfs, critical paths, exact host keys, PAM binary and
    # default runlevel without applying the U0o pre-boot trace-absence condition.
    flash.u0n_flash_v2.validate_phone_rootfs(adb, serial, local)

    block_state = flash.base.block_helper.prepare(common, adb, serial)
    common.USERDATA = block_state.node
    print("exact_userdata_node_trace_collection_preparation=passed")
    try:
        raw_output = common.adb_shell(
            adb, serial, TRACE_READ_SCRIPT, block_state.node
        )
        trace_values = values(raw_output)
        for token in (
            "trace_readonly_unmount=passed",
            "userdata_persistent_writes=no",
        ):
            if raw_output.count(token) != 1:
                raise U0oCollectError(f"trace collection marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        flash.base.restore.assert_idle(final_values, final_sections)
    finally:
        cleanup_output = flash.base.block_helper.cleanup(
            common, adb, serial, block_state
        )
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0oCollectError("userdata trace-collection node cleanup failed")
        print("exact_userdata_node_trace_collection_cleanup=passed")

    trace_state = trace_values.get("trace_state", "missing-marker")
    trace_bytes = b""
    trace_text = ""
    if trace_state == "present-regular":
        encoded = "".join(section(raw_output, "trace_base64"))
        try:
            trace_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, base64.binascii.Error) as exc:
            raise U0oCollectError(f"invalid trace base64 transport: {exc}") from exc
        expected_size = int(trace_values.get("trace_bytes", "-1"))
        if len(trace_bytes) != expected_size:
            raise U0oCollectError(
                f"trace size mismatch: decoded={len(trace_bytes)} expected={expected_size}"
            )
        if len(trace_bytes) > MAX_TRACE_BYTES:
            raise U0oCollectError(
                f"trace exceeds maximum size: {len(trace_bytes)} > {MAX_TRACE_BYTES}"
            )
        if common.sha_file_bytes(trace_bytes) if False else False:
            pass
        import hashlib

        actual_sha = hashlib.sha256(trace_bytes).hexdigest()
        if actual_sha != trace_values.get("trace_sha256"):
            raise U0oCollectError("trace SHA256 differs across ADB transport")
        trace_text = trace_bytes.decode("utf-8", errors="replace")

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0o-persistent-sshd-trace-result-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "trace-read-report.txt").write_text(raw_output, encoding="utf-8")
    if trace_state == "present-regular":
        (out / "a33x-u0o-real-boot-sshd.log").write_bytes(trace_bytes)
    shutil.copytree(observation, out / "observation")

    last_kmsg = capture_optional(
        [adb, "-s", serial, "exec-out", "cat", "/proc/last_kmsg"],
        out / "last_kmsg.bin",
        binary=True,
    )
    capture_optional(
        [adb, "-s", serial, "shell", "dmesg"],
        out / "twrp-dmesg.txt",
        binary=False,
    )
    capture_optional(
        [adb, "-s", serial, "shell", "getprop"],
        out / "twrp-getprop.txt",
        binary=False,
    )

    evidence = out / "host-evidence"
    evidence.mkdir()
    for source in (
        root / "build/a33-u0o-persistent-sshd-trace-flash.txt",
        Path(local["manifest_path"]),
        Path(local["patch_path"]),
        Path(local["audit_path"]),
        root / "build/a33-twrp-odin-restore.txt",
    ):
        if source.is_file():
            shutil.copy2(source, evidence / source.name)

    counts = trace_counts(trace_text) if trace_text else {
        key: 0 for key in COUNT_PATTERNS
    }
    metadata_valid = (
        trace_state == "present-regular"
        and trace_values.get("trace_mode") == "600"
        and trace_values.get("trace_uid") == "0"
        and trace_values.get("trace_gid") == "0"
    )
    if trace_state == "missing":
        diagnosis = "u0o-did-not-reach-persistent-trace-creation"
    elif counts["candidate_trace_open_count"] >= 1:
        diagnosis = "u0o-persistent-trace-captured"
    else:
        diagnosis = "u0o-trace-present-but-candidate-marker-missing"

    nonempty_lines = [line for line in trace_text.splitlines() if line.strip()]
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "collect-u0o-persistent-sshd-trace-read-only",
        "implementation_language": "python3",
        "adb_serial": serial,
        "candidate_sha256": flash.EXPECTED_CANDIDATE_SHA256,
        "persistent_trace_path": TRACE_PATH,
        "trace_state": trace_state,
        "trace_bytes": len(trace_bytes),
        "trace_sha256": trace_values.get("trace_sha256", ""),
        "trace_mode": trace_values.get("trace_mode", ""),
        "trace_uid": trace_values.get("trace_uid", ""),
        "trace_gid": trace_values.get("trace_gid", ""),
        "trace_metadata_valid": metadata_valid,
        "trace_line_count": len(nonempty_lines),
        "trace_first_line": nonempty_lines[0] if nonempty_lines else "",
        "trace_last_line": nonempty_lines[-1] if nonempty_lines else "",
        "trace_counts": counts,
        "diagnosis": diagnosis,
        "last_kmsg_bytes": len(last_kmsg),
        "observation_directory": str(observation),
        "reboot_transition_verified": True,
        "observation_status": observation_summary["observation_status"],
        "twrp_kernel_release": fingerprint["kernel_release"],
        "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
        "recovery_sha256": common.KNOWN_TWRP_SHA256,
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "collection_status": "passed",
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
    print(f"result_directory={out}")
    print(f"result_archive={archive}")
    print(f"result_archive_sha256={archive_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0oCollectError,
        flash.U0oFlashError,
        observer.U0oObserveError,
        flash.base.restore.cleanup.CleanupV2Error,
        flash.base.recovery_helper.ExactRecoveryNodeError,
        flash.base.restore.block_helper.ExactBlockNodeError,
        common.Refusal,
        json.JSONDecodeError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"U0o TRACE COLLECTION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

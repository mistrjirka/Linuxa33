#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
FLASH_V3_PATH = HERE / "flash-a33-u0q-emergency-ssh-v3.py"
OBSERVER_V3_PATH = HERE / "observe-a33-u0q-emergency-ssh-v3.py"
COLLECTOR_V2_PATH = HERE / "collect-a33-u0q-emergency-ssh-v2.py"
EXPECTED_FLASH_V3_BLOB = "79e8b0dd2a2a781018b027b551f54796e4608afb"
EXPECTED_OBSERVER_V3_BLOB = "37e4e8a747e6ae45f332304ae8fff1079f794cda"
EXPECTED_COLLECTOR_V2_BLOB = "23b5f665876b7053bc6cadc488a4528e1640a542"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_u0q_v3_collector_flash", FLASH_V3_PATH)
observer = load("a33_u0q_v3_collector_observer", OBSERVER_V3_PATH)
v2_collector = load("a33_u0q_v3_collector_parent", COLLECTOR_V2_PATH)
common = flash.common


class U0qV3CollectError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def latest_observation(root: Path) -> Path:
    candidates = [
        path
        for path in (root / "build/runtime-results").glob(
            "u0q-v3-emergency-ssh-observation-*"
        )
        if path.is_dir()
    ]
    if not candidates:
        raise U0qV3CollectError("no U0q v3 observation directory exists")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def capture_optional(args: list[str], destination: Path, *, binary: bool) -> bytes:
    return v2_collector.capture_optional(args, destination, binary=binary)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect U0q v3 emergency and inherited traces read-only"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    for path, expected in (
        (FLASH_V3_PATH, EXPECTED_FLASH_V3_BLOB),
        (OBSERVER_V3_PATH, EXPECTED_OBSERVER_V3_BLOB),
        (COLLECTOR_V2_PATH, EXPECTED_COLLECTOR_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0qV3CollectError(
                f"checked-in U0q v3 collector dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    observation_path = latest_observation(root)
    observation_summary_path = observation_path / "summary.json"
    if not observation_summary_path.is_file():
        raise U0qV3CollectError("latest U0q v3 observation lacks summary.json")
    observation_summary = json.loads(
        observation_summary_path.read_text(encoding="utf-8")
    )
    if observation_summary.get("candidate_sha256") != local["candidate_sha"]:
        raise U0qV3CollectError("latest observation references another candidate")
    if observation_summary.get("audit_v3_sha256") != local["audit_v3_sha"]:
        raise U0qV3CollectError("latest observation references another v3 audit")
    if observation_summary.get("reboot_transition_verified") is not True:
        raise U0qV3CollectError("latest observation did not prove TWRP transition")
    status = str(observation_summary.get("observation_status", ""))
    allowed = (
        "passed-transition-proven-u0q-v3-emergency-ssh-authenticated",
        "partial-u0q-v3-emergency-ssh-authenticated",
        "failed-u0q-v3-emergency-ssh-banner-visible",
        "failed-u0q-v3-emergency-ssh-banner-never-visible",
    )
    if not status.startswith(allowed):
        raise U0qV3CollectError(f"unexpected U0q v3 observation status: {status}")

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
            raise U0qV3CollectError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    flash.v2_flash.u0p_flash.u0o_flash.u0n_flash_v2.validate_phone_rootfs(
        adb, serial, local
    )

    block_state = flash.base.block_helper.prepare(common, adb, serial)
    common.USERDATA = block_state.node
    print("exact_userdata_node_u0q_v3_trace_collection_preparation=passed")
    try:
        raw_output = common.adb_shell(
            adb, serial, v2_collector.TRACE_READ_SCRIPT, block_state.node
        )
        trace_values = v2_collector.values(raw_output)
        for token in (
            "trace_readonly_unmount=passed",
            "userdata_persistent_writes=no",
        ):
            if raw_output.count(token) != 1:
                raise U0qV3CollectError(f"trace collection marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        flash.base.restore.assert_idle(final_values, final_sections)
    finally:
        cleanup_output = flash.base.block_helper.cleanup(
            common, adb, serial, block_state
        )
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0qV3CollectError("userdata trace node cleanup failed")
        print("exact_userdata_node_u0q_v3_trace_collection_cleanup=passed")

    emergency_bytes, emergency_text = v2_collector.decode_trace(
        raw_output, trace_values, "emergency"
    )
    inherited_bytes, inherited_text = v2_collector.decode_trace(
        raw_output, trace_values, "inherited"
    )
    emergency_counts = v2_collector.count_patterns(
        emergency_text, v2_collector.EMERGENCY_COUNT_PATTERNS
    )
    inherited_counts = v2_collector.count_patterns(
        inherited_text, v2_collector.INHERITED_COUNT_PATTERNS
    )

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0q-v3-emergency-ssh-result-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "trace-read-report.txt").write_text(raw_output, encoding="utf-8")
    if emergency_bytes:
        (out / "a33x-u0q-emergency-ssh.log").write_bytes(emergency_bytes)
    if inherited_bytes:
        (out / "a33x-u0o-real-boot-sshd.log").write_bytes(inherited_bytes)
    shutil.copytree(observation_path, out / "observation")

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
        root / "build/a33-u0q-v3-emergency-ssh-flash.txt",
        Path(local["manifest_path"]),
        Path(local["patch_path"]),
        Path(local["base_audit_path"]),
        Path(local["audit_v3_path"]),
        root / "build/a33-twrp-odin-restore.txt",
    ):
        if source.is_file():
            shutil.copy2(source, evidence / source.name)

    emergency_state = trace_values.get("emergency_state", "missing-marker")
    inherited_state = trace_values.get("inherited_state", "missing-marker")
    emergency_metadata_valid = (
        emergency_state == "present-regular"
        and trace_values.get("emergency_mode") == "600"
        and trace_values.get("emergency_uid") == "0"
        and trace_values.get("emergency_gid") == "0"
    )
    inherited_metadata_valid = (
        inherited_state == "present-regular"
        and trace_values.get("inherited_mode") == "600"
        and trace_values.get("inherited_uid") == "0"
        and trace_values.get("inherited_gid") == "0"
    )

    if emergency_state == "missing":
        diagnosis = "u0q-v3-did-not-reach-emergency-trace-creation"
    elif emergency_counts["candidate_trace_open_count"] < 1:
        diagnosis = "u0q-v3-trace-present-but-candidate-marker-missing"
    elif "event=runtime-mounts-ready" not in emergency_text:
        diagnosis = "u0q-v3-runtime-mount-preparation-did-not-complete"
    elif emergency_counts["config_test_passed_count"] < 1:
        diagnosis = "u0q-v3-emergency-sshd-config-test-did-not-pass"
    elif emergency_counts["sshd_helper_spawned_count"] < 1:
        diagnosis = "u0q-v3-emergency-sshd-not-spawned"
    elif emergency_counts["network_configured_count"] < 1:
        diagnosis = "u0q-v3-usb-network-never-configured"
    elif emergency_counts["pre_switch_root_ready_count"] < 1:
        diagnosis = "u0q-v3-listener-or-network-not-ready-before-switch-root"
    elif observation_summary.get("first_emergency_ssh_auth_seconds") is None:
        diagnosis = "u0q-v3-live-channel-ready-but-host-authentication-failed"
    elif observation_summary.get("live_diagnostics_returncode") != 0:
        diagnosis = "u0q-v3-authenticated-but-live-diagnostics-command-failed"
    else:
        diagnosis = "u0q-v3-live-emergency-ssh-and-diagnostics-succeeded"

    emergency_lines = [line for line in emergency_text.splitlines() if line.strip()]
    inherited_lines = [line for line in inherited_text.splitlines() if line.strip()]
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "collect-u0q-v3-emergency-ssh-traces-read-only",
        "implementation_language": "python3",
        "adb_serial": serial,
        "candidate_sha256": local["candidate_sha"],
        "manifest_sha256": local["manifest_sha"],
        "audit_v3_sha256": local["audit_v3_sha"],
        "runtime_mount_policy": flash.builder_v3.MOUNT_POLICY,
        "observation_directory": str(observation_path),
        "observation_status": status,
        "reboot_transition_verified": True,
        "emergency_ssh_authenticated": (
            observation_summary.get("first_emergency_ssh_auth_seconds") is not None
        ),
        "live_diagnostics_returncode": observation_summary.get(
            "live_diagnostics_returncode"
        ),
        "emergency_trace_path": flash.v2_flash.EMERGENCY_TRACE_PATH,
        "emergency_trace_state": emergency_state,
        "emergency_trace_bytes": len(emergency_bytes),
        "emergency_trace_sha256": trace_values.get("emergency_sha256", ""),
        "emergency_trace_metadata_valid": emergency_metadata_valid,
        "emergency_trace_line_count": len(emergency_lines),
        "emergency_trace_first_line": emergency_lines[0] if emergency_lines else "",
        "emergency_trace_last_line": emergency_lines[-1] if emergency_lines else "",
        "emergency_trace_counts": emergency_counts,
        "inherited_trace_path": flash.v2_flash.PARENT_TRACE_PATH,
        "inherited_trace_state": inherited_state,
        "inherited_trace_bytes": len(inherited_bytes),
        "inherited_trace_sha256": trace_values.get("inherited_sha256", ""),
        "inherited_trace_metadata_valid": inherited_metadata_valid,
        "inherited_trace_line_count": len(inherited_lines),
        "inherited_trace_counts": inherited_counts,
        "diagnosis": diagnosis,
        "last_kmsg_bytes": len(last_kmsg),
        "recovery_sha256": common.KNOWN_TWRP_SHA256,
        "twrp_kernel_release": fingerprint["kernel_release"],
        "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
        "phone_partition_writes": "no",
        "phone_reboot_performed": "no",
        "userdata_persistent_writes": "no",
        "collection_status": "passed",
    }
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)
    archive_sha = hashlib.sha256(archive.read_bytes()).hexdigest()
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"result_directory={out}")
    print(f"result_archive={archive}")
    print(f"result_archive_sha256={archive_sha}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0qV3CollectError,
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
        OSError,
        ValueError,
    ) as exc:
        print(f"U0q V3 COLLECTOR FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

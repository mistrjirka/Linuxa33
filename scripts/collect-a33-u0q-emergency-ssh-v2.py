#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tarfile

HERE = Path(__file__).resolve().parent
FLASH_PATH = HERE / "flash-a33-u0q-emergency-ssh-v2.py"
OBSERVER_PATH = HERE / "observe-a33-u0q-emergency-ssh-v2.py"
U0P_COLLECTOR_PATH = HERE / "collect-a33-u0p-corrected-sshd-source-hash.py"
EXPECTED_FLASH_BLOB = "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
EXPECTED_OBSERVER_BLOB = "1b8bcc6a917214cd89abdc38c10af1f2a42ff449"
EXPECTED_U0P_COLLECTOR_BLOB = "cfa03e126794565af9566ce6f9e4675aa5f2ef02"
MAX_TRACE_BYTES = 4 * 1024 * 1024


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_u0q_v2_collector_flash", FLASH_PATH)
observer = load("a33_u0q_v2_collector_observer", OBSERVER_PATH)
u0p_collector = load("a33_u0q_v2_collector_u0p", U0P_COLLECTOR_PATH)
common = flash.common


class U0qV2CollectError(RuntimeError):
    pass


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)], check=False
    ).stdout.strip()


def values(text: str) -> dict[str, str]:
    return u0p_collector.values(text)


def section(text: str, name: str) -> list[str]:
    return u0p_collector.section(text, name)


def latest_observation(root: Path) -> Path:
    candidates = [
        path
        for path in (root / "build/runtime-results").glob(
            "u0q-v2-emergency-ssh-observation-*"
        )
        if path.is_dir()
    ]
    if not candidates:
        raise U0qV2CollectError("no U0q v2 observation directory exists")
    return max(candidates, key=lambda path: path.stat().st_mtime)


TRACE_READ_SCRIPT = rf'''set -eu
target="$1"
mountpoint=/tmp/a33x-u0q-v2-trace-collect
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
emit_trace()
{{
    name="$1"
    path="$2"
    echo "${{name}}_path=$path"
    if [ ! -e "$path" ]; then
        echo "${{name}}_state=missing"
    elif [ ! -f "$path" ]; then
        echo "${{name}}_state=present-not-regular"
    else
        echo "${{name}}_state=present-regular"
        echo "${{name}}_mode=$(stat -c '%a' "$path")"
        echo "${{name}}_uid=$(stat -c '%u' "$path")"
        echo "${{name}}_gid=$(stat -c '%g' "$path")"
        echo "${{name}}_bytes=$(stat -c '%s' "$path")"
        echo "${{name}}_sha256=$(sha256sum "$path" | awk 'NR==1 {{print $1}}')"
        echo "${{name}}_base64_begin"
        base64 "$path"
        echo "${{name}}_base64_end"
    fi
}}
emit_trace emergency "$mountpoint{flash.EMERGENCY_TRACE_PATH}"
emit_trace inherited "$mountpoint{flash.PARENT_TRACE_PATH}"
umount "$mountpoint"
mounted=no
echo "trace_readonly_unmount=passed"
echo "userdata_persistent_writes=no"
'''


EMERGENCY_COUNT_PATTERNS = {
    "candidate_trace_open_count": r"candidate=U0q-emergency-ssh stage=trace-open",
    "runtime_directory_ready_count": r"event=runtime-directory-ready",
    "network_helper_spawned_count": r"event=network-helper-spawned",
    "network_helper_started_count": r"event=network-helper-started",
    "network_configured_count": r"event=network-configured",
    "network_ready_marker_count": r"event=network-ready-marker-written",
    "config_test_start_count": r"event=config-test-start",
    "config_test_passed_count": r"event=config-test-passed",
    "sshd_helper_spawned_count": r"event=sshd-helper-spawned",
    "pre_switch_root_wait_count": r"event=pre-switch-root-wait",
    "pre_switch_root_ready_count": r"event=pre-switch-root-ready",
    "runtime_firewall_rule_added_count": r"event=runtime-firewall-rule-added",
    "runtime_firewall_rule_present_count": r"event=runtime-firewall-rule-present",
    "runtime_firewall_wait_count": r"event=runtime-firewall-table-wait",
    "sshd_listening_count": r"Server listening on|listening on .*port 2222",
    "accepted_publickey_count": r"Accepted publickey|publickey authentication accepted",
    "connection_count": r"Connection from|Accepted connection",
    "error_count": r"(?:^|\s)error=|fatal:|error:",
}

INHERITED_COUNT_PATTERNS = {
    "u0p_candidate_count": r"candidate=U0p-corrected-sshd-source-hash",
    "setup_success_count": r"stage=setup-success",
    "switch_root_ready_count": r"stage=switch-root-ready",
    "script_loaded_count": r"event=script-loaded",
    "checkconfig_enter_count": r"event=checkconfig-enter",
    "start_pre_enter_count": r"event=start-pre-enter",
    "monitor_started_count": r"event=monitor-started",
    "snapshot_count": r"event=snapshot",
    "listener_yes_count": r"event=snapshot[^\n]*listener=yes",
    "error_count": r"(?:^|\s)error=",
}


def count_patterns(text: str, patterns: dict[str, str]) -> dict[str, int]:
    return {
        key: len(re.findall(pattern, text, flags=re.IGNORECASE))
        for key, pattern in patterns.items()
    }


def decode_trace(
    raw_output: str, trace_values: dict[str, str], name: str
) -> tuple[bytes, str]:
    state = trace_values.get(f"{name}_state", "missing-marker")
    if state != "present-regular":
        return b"", ""
    encoded = "".join(section(raw_output, f"{name}_base64"))
    try:
        payload = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise U0qV2CollectError(f"invalid {name} trace base64: {exc}") from exc
    expected_size = int(trace_values.get(f"{name}_bytes", "-1"))
    if len(payload) != expected_size:
        raise U0qV2CollectError(
            f"{name} trace size mismatch: decoded={len(payload)} expected={expected_size}"
        )
    if len(payload) > MAX_TRACE_BYTES:
        raise U0qV2CollectError(
            f"{name} trace exceeds maximum: {len(payload)} > {MAX_TRACE_BYTES}"
        )
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != trace_values.get(f"{name}_sha256"):
        raise U0qV2CollectError(f"{name} trace SHA differs across ADB transport")
    return payload, payload.decode("utf-8", errors="replace")


def capture_optional(args: list[str], destination: Path, *, binary: bool) -> bytes:
    return u0p_collector.capture_optional(args, destination, binary=binary)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Collect U0q v2 emergency and inherited traces read-only"
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
        (U0P_COLLECTOR_PATH, EXPECTED_U0P_COLLECTOR_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise U0qV2CollectError(
                f"checked-in U0q v2 collector dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )

    local = flash.local_evidence(root, repo)
    observation = latest_observation(root)
    observation_summary_path = observation / "summary.json"
    if not observation_summary_path.is_file():
        raise U0qV2CollectError("latest U0q observation lacks summary.json")
    observation_summary = json.loads(
        observation_summary_path.read_text(encoding="utf-8")
    )
    if observation_summary.get("candidate_sha256") != local["candidate_sha"]:
        raise U0qV2CollectError("latest observation references another candidate")
    if observation_summary.get("reboot_transition_verified") is not True:
        raise U0qV2CollectError("latest observation did not prove TWRP transition")
    allowed_status_prefixes = (
        "passed-transition-proven-emergency-ssh-authenticated",
        "partial-emergency-ssh-authenticated",
        "failed-emergency-ssh-banner-visible",
        "failed-emergency-ssh-banner-never-visible",
    )
    status = str(observation_summary.get("observation_status", ""))
    if not status.startswith(allowed_status_prefixes):
        raise U0qV2CollectError(f"unexpected U0q observation status: {status}")

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
            raise U0qV2CollectError("temporary recovery node cleanup failed")
        print("exact_recovery_node_cleanup=passed")

    # Validate the installed rootfs and host keys, but do not require the preboot
    # trace baseline because U0q intentionally created its own trace.
    flash.u0p_flash.u0o_flash.u0n_flash_v2.validate_phone_rootfs(
        adb, serial, local
    )

    block_state = flash.base.block_helper.prepare(common, adb, serial)
    common.USERDATA = block_state.node
    print("exact_userdata_node_u0q_v2_trace_collection_preparation=passed")
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
                raise U0qV2CollectError(f"trace collection marker missing: {token}")
        final_values, final_sections = common.live_state(adb, serial)
        flash.base.restore.assert_idle(final_values, final_sections)
    finally:
        cleanup_output = flash.base.block_helper.cleanup(
            common, adb, serial, block_state
        )
        if cleanup_output.count("exact_block_node_cleanup_status=passed") != 1:
            raise U0qV2CollectError("userdata trace node cleanup failed")
        print("exact_userdata_node_u0q_v2_trace_collection_cleanup=passed")

    emergency_bytes, emergency_text = decode_trace(
        raw_output, trace_values, "emergency"
    )
    inherited_bytes, inherited_text = decode_trace(
        raw_output, trace_values, "inherited"
    )

    result_root = root / "build/runtime-results"
    result_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = result_root / f"u0q-v2-emergency-ssh-result-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    (out / "trace-read-report.txt").write_text(raw_output, encoding="utf-8")
    if emergency_bytes:
        (out / "a33x-u0q-emergency-ssh.log").write_bytes(emergency_bytes)
    if inherited_bytes:
        (out / "a33x-u0o-real-boot-sshd.log").write_bytes(inherited_bytes)
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
        root / "build/a33-u0q-v2-emergency-ssh-flash.txt",
        Path(local["manifest_path"]),
        Path(local["patch_path"]),
        Path(local["base_audit_path"]),
        Path(local["audit_v2_path"]),
        root / "build/a33-twrp-odin-restore.txt",
    ):
        if source.is_file():
            shutil.copy2(source, evidence / source.name)

    emergency_counts = count_patterns(emergency_text, EMERGENCY_COUNT_PATTERNS)
    inherited_counts = count_patterns(inherited_text, INHERITED_COUNT_PATTERNS)
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
        diagnosis = "u0q-did-not-reach-emergency-trace-creation"
    elif emergency_counts["candidate_trace_open_count"] < 1:
        diagnosis = "u0q-trace-present-but-candidate-marker-missing"
    elif emergency_counts["config_test_passed_count"] < 1:
        diagnosis = "u0q-emergency-sshd-config-test-did-not-pass"
    elif emergency_counts["sshd_helper_spawned_count"] < 1:
        diagnosis = "u0q-emergency-sshd-not-spawned"
    elif emergency_counts["network_configured_count"] < 1:
        diagnosis = "u0q-usb-network-never-configured"
    elif emergency_counts["pre_switch_root_ready_count"] < 1:
        diagnosis = "u0q-listener-or-network-not-ready-before-switch-root"
    elif observation_summary.get("first_emergency_ssh_auth_seconds") is None:
        diagnosis = "u0q-live-channel-ready-but-host-authentication-failed"
    elif observation_summary.get("live_diagnostics_returncode") != 0:
        diagnosis = "u0q-authenticated-but-live-diagnostics-command-failed"
    else:
        diagnosis = "u0q-live-emergency-ssh-and-diagnostics-succeeded"

    emergency_lines = [line for line in emergency_text.splitlines() if line.strip()]
    inherited_lines = [line for line in inherited_text.splitlines() if line.strip()]
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "collect-u0q-v2-emergency-ssh-traces-read-only",
        "implementation_language": "python3",
        "adb_serial": serial,
        "candidate_sha256": local["candidate_sha"],
        "manifest_sha256": local["manifest_sha"],
        "audit_v2_sha256": local["audit_v2_sha"],
        "observation_directory": str(observation),
        "observation_status": status,
        "reboot_transition_verified": True,
        "emergency_ssh_authenticated": (
            observation_summary.get("first_emergency_ssh_auth_seconds") is not None
        ),
        "live_diagnostics_returncode": observation_summary.get(
            "live_diagnostics_returncode"
        ),
        "emergency_trace_path": flash.EMERGENCY_TRACE_PATH,
        "emergency_trace_state": emergency_state,
        "emergency_trace_bytes": len(emergency_bytes),
        "emergency_trace_sha256": trace_values.get("emergency_sha256", ""),
        "emergency_trace_metadata_valid": emergency_metadata_valid,
        "emergency_trace_line_count": len(emergency_lines),
        "emergency_trace_first_line": emergency_lines[0] if emergency_lines else "",
        "emergency_trace_last_line": emergency_lines[-1] if emergency_lines else "",
        "emergency_trace_counts": emergency_counts,
        "inherited_trace_path": flash.PARENT_TRACE_PATH,
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
        U0qV2CollectError,
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
        OSError,
        ValueError,
    ) as exc:
        print(f"U0q V2 COLLECTOR FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

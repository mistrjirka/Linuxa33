#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
FLASH_PATH = HERE / "flash-a33-u0m-watchdog-magic-close-v4.py"
CLEANUP_PATH = HERE / "cleanup-a33-openrc-sshd-chroot-v2.py"
EXPECTED_FLASH_BLOB = "a4523f358e853026279bc780feeb3c5306c2ea29"
EXPECTED_CLEANUP_BLOB = "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
EXPECTED_TWRP_SHA256 = "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash = load("a33_rootfs_readonly_flash", FLASH_PATH)
cleanup = load("a33_rootfs_readonly_cleanup", CLEANUP_PATH)
common = flash.base.common


class ValidationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_blob(repo: Path, path: Path) -> str:
    return common.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        check=False,
    ).stdout.strip()


def recovery_gate(observed_sha: str) -> str:
    if observed_sha == EXPECTED_TWRP_SHA256:
        return "recovery-partition-sha256-and-runtime-fingerprint"
    if observed_sha == "":
        return "runtime-fingerprint-recovery-path-unreadable"
    raise ValidationError(
        "recovery partition is readable but does not match exact TWRP: "
        f"actual={observed_sha!r} expected={EXPECTED_TWRP_SHA256!r}"
    )


def parse_critical_hashes(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in output.splitlines():
        match = re.fullmatch(r"critical_sha=([0-9a-f]{64}) path=(/.+)", line)
        if match:
            result[match.group(2)] = match.group(1)
    return result


def assert_userdata_idle(values: dict[str, str], sections: dict[str, list[str]]) -> None:
    expected = {
        "userdata_resolved": common.EXPECTED_USERDATA,
        "userdata_bytes": str(common.EXPECTED_USERDATA_BYTES),
        "userdata_readonly": "0",
    }
    mismatches = [
        f"{key}: actual={values.get(key)!r} expected={wanted!r}"
        for key, wanted in expected.items()
        if values.get(key) != wanted
    ]
    for name in ("mount_users", "swap_users", "dm_users"):
        active = [item for item in sections.get(name, []) if item]
        if active:
            mismatches.append(f"{name}: active={active!r}")
    if mismatches:
        raise ValidationError("userdata is not idle and safe:\n" + "\n".join(mismatches))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate the installed A33 rootfs and exact U0m ancestry read-only, "
            "without flashing or rebooting"
        )
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default="adb")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
    adb = shutil.which(args.adb) or args.adb

    pins = (
        (FLASH_PATH, EXPECTED_FLASH_BLOB),
        (CLEANUP_PATH, EXPECTED_CLEANUP_BLOB),
    )
    for path, expected in pins:
        actual = git_blob(repo, path)
        if actual != expected:
            raise ValidationError(
                f"checked-in dependency changed: {path.name} actual={actual} expected={expected}"
            )

    local = flash.base.validate_local(root, repo)
    print("local_u0m_artifact_validation=passed")
    print(f"candidate_sha256={local['candidate_sha']}")
    print("phone_partition_writes=no")

    serial = common.select_recovery(adb, 30)
    values, sections = common.live_state(adb, serial)
    fingerprint = cleanup.validate_runtime_fingerprint(adb, serial)
    gate_source = recovery_gate(values.get("recovery_sha", ""))
    assert_userdata_idle(values, sections)

    actual_uuid, actual_label = common.ext4_identity(adb, serial)
    if actual_uuid != local["root_uuid"] or actual_label != "pmOS_root":
        raise ValidationError(
            "installed rootfs identity mismatch: "
            f"uuid={actual_uuid!r} label={actual_label!r} "
            f"expected_uuid={local['root_uuid']!r} expected_label='pmOS_root'"
        )

    verify_output = common.adb_shell(
        adb,
        serial,
        common.VERIFY_SCRIPT,
        common.USERDATA,
        str(local["root_uuid"]),
        *common.CRITICAL_PATHS,
    )
    actual_critical = parse_critical_hashes(verify_output)
    if actual_critical != local["critical"]:
        raise ValidationError(
            "installed rootfs critical content mismatch: "
            f"expected={local['critical']} actual={actual_critical}"
        )
    if verify_output.count("readonly_verification=passed") != 1:
        raise ValidationError("read-only rootfs verification did not pass exactly once")
    if verify_output.count("readonly_unmount=passed") != 1:
        raise ValidationError("read-only rootfs verification did not unmount exactly once")

    final_values, final_sections = common.live_state(adb, serial)
    assert_userdata_idle(final_values, final_sections)

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = root / "build/runtime-results" / f"a33-installed-rootfs-readonly-validation-{timestamp}"
    out.mkdir(parents=True, exist_ok=False)
    raw = out / "verification.txt"
    raw.write_text(verify_output, encoding="utf-8")
    summary = {
        "created": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "operation": "validate-a33-installed-rootfs-read-only",
        "implementation_language": "python3",
        "adb_serial": serial,
        "validation_status": "passed",
        "twrp_gate_source": gate_source,
        "observed_recovery_partition_sha256": values.get("recovery_sha", ""),
        "twrp_kernel_release": fingerprint["kernel_release"],
        "twrp_config_gz_sha256": fingerprint["config_gz_sha256"],
        "userdata_resolved": values["userdata_resolved"],
        "userdata_filesystem_uuid": actual_uuid,
        "userdata_filesystem_label": actual_label,
        "userdata_idle_before": True,
        "userdata_idle_after": True,
        "critical_path_count": len(actual_critical),
        "critical_hashes": actual_critical,
        "candidate_sha256": local["candidate_sha"],
        "candidate_manifest": str(local["manifest_path"]),
        "candidate_manifest_sha256": sha256_file(Path(local["manifest_path"])),
        "userdata_persistent_writes": "no",
        "recovery_written": "no",
        "boot_written": "no",
        "super_written": "no",
        "phone_reboot_performed": "no",
        "raw_report": str(raw),
        "raw_report_sha256": sha256_file(raw),
    }
    summary_path = out / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    archive = out.with_suffix(".tar.gz")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(out, arcname=out.name)

    print(verify_output, end="" if verify_output.endswith("\n") else "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"validation_directory={out}")
    print(f"validation_archive={archive}")
    print(f"validation_archive_sha256={sha256_file(archive)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValidationError, cleanup.CleanupV2Error, common.Refusal, OSError, ValueError) as exc:
        print(f"A33 INSTALLED ROOTFS READ-ONLY VALIDATION FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

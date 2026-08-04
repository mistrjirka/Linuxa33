#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
COMMON_PATH = HERE / "flash-a33-u0i-python-direct-root-v2.py"
spec = importlib.util.spec_from_file_location("a33_u0i_flash_common", COMMON_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load common flash implementation: {COMMON_PATH}")
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)

sys.path.insert(0, str(HERE / "lib"))
from a33_rootfs_recovery_flash import FlashProfile, execute_flash

EXPECTED_CONSUMERS = (
    "init_functions_2nd.sh:resize_root_partition,"
    "init_functions_2nd.sh:resize_root_filesystem,"
    "init_functions.sh:mount_root_partition"
)
PROFILE = FlashProfile(
    operation="flash-exact-u0j-root-api-compatible",
    report_name="a33-first-rootfs-u0j-root-api-compatible-flash.txt",
    remote_candidate="/tmp/a33x-u0j-root-api-compatible-recovery.img",
    success_label="U0j root-API-compatible",
)


def manifest_contract() -> dict[str, str]:
    return {
        "candidate": "U0j-root-api-compatible",
        "implementation_language": "python3",
        "functional_base": "U0i-python-direct-root-v2",
        "functional_delta": "make-find-root-partition-support-stdout-and-output-variable",
        "kernel_cmdline_delta": "none",
        "module_delta": "none",
        "forced_root": common.EXPECTED_USERDATA,
        "cpio_entry_order_preserved": "yes",
        "cpio_metadata_preserved_except_target_size_and_crc": "yes",
        "cpio_payload_delta": "init_functions.sh",
        "shell_delta": "find_root_partition",
        "shell_text_outside_find_root_partition_preserved": "yes",
        "wait_root_function_preserved": "yes",
        "find_root_stdout_api": "passed",
        "find_root_output_variable_api": "partition",
        "find_root_output_variable_consumers": EXPECTED_CONSUMERS,
        "caller_local_partition_contract": "passed",
        "embedded_modules": "67",
        "direct_root_identity_recheck": "yes",
        "second_stage_order_validation": "passed",
        "preparation_status": "passed",
        "phone_partition_writes": "no",
        "build_status": "passed",
    }


def patch_contract() -> dict[str, str]:
    return {
        "operation": "python-byte-preserving-fix-find-root-dual-api",
        "implementation_language": "python3",
        "functional_base": "U0i-python-direct-root-v2",
        "cpio_entry_order_preserved": "yes",
        "cpio_metadata_preserved_except_target_size_and_crc": "yes",
        "cpio_payload_delta": "init_functions.sh",
        "shell_delta": "find_root_partition",
        "shell_text_outside_find_root_partition_preserved": "yes",
        "wait_root_function_preserved": "yes",
        "find_root_stdout_api": "passed",
        "find_root_output_variable_api": "partition",
        "find_root_stdout_call_count": "4",
        "find_root_output_variable_call_count": "3",
        "find_root_output_variable_consumers": EXPECTED_CONSUMERS,
        "caller_local_partition_contract": "passed",
        "direct_root_identity_recheck": "yes",
        "forced_root": common.EXPECTED_USERDATA,
        "embedded_modules": "67",
        "patch_status": "passed",
        "phone_partition_writes": "no",
        "second_stage_order_validation": "passed",
    }


def validate_local(root: Path, repo: Path) -> dict[str, object]:
    # Reuse the already-tested U0i validation for the deployed rootfs, U0h
    # ancestry, exact deployment image and critical-file manifest.
    local = common.validate_local(root, repo)
    u0i_manifest = common.kv(Path(local["manifest_path"]))

    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0j-root-api-compatible-manifest.txt"
    if not manifest_path.is_file():
        common.refuse(f"missing U0j manifest: {manifest_path}")
    manifest = common.kv(manifest_path)
    common.require(manifest, manifest_contract(), "U0j manifest")
    common.git_commit_available(repo, manifest.get("linuxa33_commit", ""))

    candidate = Path(manifest.get("recovery", ""))
    candidate_sha = manifest.get("recovery_sha256", "")
    try:
        candidate_size = int(manifest.get("recovery_size", ""))
    except ValueError:
        common.refuse("invalid U0j recovery_size")
    if candidate_size != 100663296 or not re.fullmatch(r"[0-9a-f]{64}", candidate_sha):
        common.refuse("invalid U0j recovery size or SHA256 contract")
    if (
        not candidate.is_file()
        or candidate.stat().st_size != candidate_size
        or common.sha_file(candidate) != candidate_sha
    ):
        common.refuse("U0j candidate differs from its manifest")

    patch_report = Path(manifest.get("patch_report", ""))
    patch_sha = manifest.get("patch_report_sha256", "")
    if not patch_report.is_file() or common.sha_file(patch_report) != patch_sha:
        common.refuse("U0j patch report differs from its manifest")
    patch = common.kv(patch_report)
    common.require(patch, patch_contract(), "U0j patch report")

    if patch.get("u0j_initramfs_sha256") != manifest.get("u0j_initramfs_sha256"):
        common.refuse("U0j patch report and manifest disagree on initramfs hash")
    if patch.get("u0i_initramfs_sha256") != manifest.get("u0i_initramfs_sha256"):
        common.refuse("U0j patch report and manifest disagree on U0i ancestry")
    if manifest.get("u0i_initramfs_sha256") != u0i_manifest.get("u0i_initramfs_sha256"):
        common.refuse("U0j manifest does not descend from the validated U0i initramfs")

    u0j_image = Path(manifest.get("u0j_initramfs", ""))
    if not u0j_image.is_file() or common.sha_file(u0j_image) != manifest.get(
        "u0j_initramfs_sha256"
    ):
        common.refuse("local U0j initramfs differs from the manifest")

    local.update(
        {
            "manifest_path": manifest_path,
            "candidate": candidate,
            "candidate_sha": candidate_sha,
            "candidate_size": candidate_size,
        }
    )
    return local


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash exact U0j recovery through the shared Python validation pipeline"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    root, repo = args.root.resolve(), args.repo.resolve()
    local = validate_local(root, repo)
    return execute_flash(
        common,
        PROFILE,
        root=root,
        adb_argument=args.adb,
        preflight_only=args.preflight_only,
        local=local,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except common.Refusal as exc:
        print(f"REFUSING U0j flash: {exc}", file=sys.stderr)
        raise SystemExit(1)

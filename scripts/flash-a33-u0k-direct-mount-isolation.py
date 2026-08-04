#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import importlib.util
import os
from pathlib import Path
import re
import sys

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
    "a33_u0k_flash_common",
    HERE / "flash-a33-u0i-python-direct-root-v2.py",
)
u0j_flash = load(
    "a33_u0k_parent_flash",
    HERE / "flash-a33-u0j-root-api-compatible.py",
)
u0k_builder = load(
    "a33_u0k_builder_contract",
    HERE / "make-u0k-direct-mount-isolation.py",
)

sys.path.insert(0, str(HERE / "lib"))
from a33_rootfs_recovery_flash import FlashProfile, execute_flash

SKIPPED_CALLS = ",".join(u0k_builder.SKIPPED_CALLS)
STAGE_MARKERS = ",".join(u0k_builder.MARKERS)
PROFILE = FlashProfile(
    operation="flash-exact-u0k-direct-mount-isolation",
    report_name="a33-first-rootfs-u0k-direct-mount-isolation-flash.txt",
    remote_candidate="/tmp/a33x-u0k-direct-mount-isolation-recovery.img",
    success_label="U0k direct-mount isolation",
)


def common_contract() -> dict[str, str]:
    return {
        "implementation_language": "python3",
        "functional_base": "U0j-root-api-compatible",
        "u0j_initramfs_sha256": "aaf2f4bda5e253f5e42ec86ea699ac0385cafa0185d1f93850ef4f9e63ab3f2f",
        "cpio_entry_count": "420",
        "cpio_entry_order_preserved": "yes",
        "cpio_metadata_preserved_except_target_size_and_crc": "yes",
        "cpio_payload_delta": "init_2nd.sh",
        "shell_delta": "second-stage-root-handoff-sequence-only",
        "shell_text_outside_two_exact_blocks_preserved": "yes",
        "root_discovery_preserved": "yes",
        "rootfs_check_skipped": "yes",
        "rootfs_partition_resize_skipped": "yes",
        "rootfs_filesystem_resize_skipped": "yes",
        "rootfs_unlock_skipped": "yes",
        "old_install_partition_delete_skipped": "yes",
        "legacy_boot_partition_mount_skipped": "yes",
        "mount_root_partition_retained": "yes",
        "post_mount_resize_hook_retained": "yes",
        "switch_root_retained": "yes",
        "skipped_second_stage_calls": SKIPPED_CALLS,
        "stage_markers": STAGE_MARKERS,
        "embedded_modules": "67",
        "phone_partition_writes": "no",
    }


def manifest_contract() -> dict[str, str]:
    return {
        "candidate": "U0k-direct-mount-isolation",
        "functional_delta": "skip-first-boot-resize-and-legacy-boot-mount-then-directly-mount-root",
        "kernel_cmdline_delta": "none",
        "module_delta": "none",
        "preparation_status": "passed",
        "build_status": "passed",
        **common_contract(),
    }


def patch_contract() -> dict[str, str]:
    return {
        "operation": "python-byte-preserving-direct-mount-isolation",
        "patch_status": "passed",
        **common_contract(),
    }


def require_sha(value: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        common.refuse(f"invalid SHA256 in U0k {label}: {value!r}")


def validate_local(root: Path, repo: Path) -> dict[str, object]:
    # Reuse the deployed-rootfs, U0h, U0i and U0j artifact validation. This is
    # local-only; it does not require U0j to be flashed on the phone.
    local = u0j_flash.validate_local(root, repo)
    u0j_manifest_path = Path(local["manifest_path"])
    u0j_manifest = common.kv(u0j_manifest_path)

    manifest_path = root / "build/candidates/a33x-h1-usbpd-u0k-direct-mount-isolation-manifest.txt"
    expected_candidate = root / "build/candidates/a33x-h1-usbpd-u0k-direct-mount-isolation-recovery.img"
    expected_patch = root / "build/u0k-direct-mount-isolation-patch.txt"
    expected_initramfs = root / "export-u0k-direct-mount-isolation/initramfs"

    if not manifest_path.is_file():
        common.refuse(f"missing U0k manifest: {manifest_path}")
    manifest = common.kv(manifest_path)
    common.require(manifest, manifest_contract(), "U0k manifest")
    common.git_commit_available(repo, manifest.get("linuxa33_commit", ""))

    candidate = Path(manifest.get("recovery", ""))
    if candidate.resolve() != expected_candidate.resolve():
        common.refuse("U0k manifest references an unexpected recovery path")
    try:
        candidate_size = int(manifest.get("recovery_size", ""))
    except ValueError:
        common.refuse("invalid U0k recovery_size")
    candidate_sha = manifest.get("recovery_sha256", "")
    require_sha(candidate_sha, "recovery")
    if (
        candidate_size != 100663296
        or not candidate.is_file()
        or candidate.stat().st_size != candidate_size
        or common.sha_file(candidate) != candidate_sha
    ):
        common.refuse("U0k recovery differs from its manifest")

    patch_report = Path(manifest.get("patch_report", ""))
    if patch_report.resolve() != expected_patch.resolve():
        common.refuse("U0k manifest references an unexpected patch report")
    patch_sha = manifest.get("patch_report_sha256", "")
    require_sha(patch_sha, "patch report")
    if not patch_report.is_file() or common.sha_file(patch_report) != patch_sha:
        common.refuse("U0k patch report differs from its manifest")
    patch = common.kv(patch_report)
    common.require(patch, patch_contract(), "U0k patch report")

    for key in (
        "u0j_initramfs_sha256",
        "u0k_initramfs_sha256",
        "original_init_2nd_sha256",
        "patched_init_2nd_sha256",
    ):
        require_sha(patch.get(key, ""), f"patch field {key}")
    for key in common_contract():
        if patch.get(key) != manifest.get(key):
            common.refuse(f"U0k manifest and patch report disagree on {key}")
    if patch.get("u0k_initramfs_sha256") != manifest.get("u0k_initramfs_sha256"):
        common.refuse("U0k manifest and patch report disagree on initramfs SHA256")
    if patch.get("u0j_initramfs_sha256") != u0j_manifest.get("u0j_initramfs_sha256"):
        common.refuse("U0k does not descend from the validated U0j initramfs")

    u0j_initramfs = Path(manifest.get("u0j_initramfs", ""))
    u0k_initramfs = Path(manifest.get("u0k_initramfs", ""))
    if u0k_initramfs.resolve() != expected_initramfs.resolve():
        common.refuse("U0k manifest references an unexpected initramfs path")
    if (
        not u0j_initramfs.is_file()
        or common.sha_file(u0j_initramfs) != manifest.get("u0j_initramfs_sha256")
    ):
        common.refuse("U0j initramfs differs from U0k ancestry contract")
    if (
        not u0k_initramfs.is_file()
        or common.sha_file(u0k_initramfs) != manifest.get("u0k_initramfs_sha256")
    ):
        common.refuse("U0k initramfs differs from its manifest")

    try:
        before = u0k_builder.v2.Archive.parse(gzip.decompress(u0j_initramfs.read_bytes()))
        after = u0k_builder.v2.Archive.parse(gzip.decompress(u0k_initramfs.read_bytes()))
    except (OSError, u0k_builder.v2.CpioError) as exc:
        common.refuse(f"cannot parse U0j/U0k initramfs: {exc}")
    before.assert_only_payload_changed(after, u0k_builder.TARGET)
    original_init2 = before.one(u0k_builder.TARGET).data.decode("utf-8", "strict")
    actual_init2 = after.one(u0k_builder.TARGET).data.decode("utf-8", "strict")
    expected_init2 = u0k_builder.patch_second_stage(original_init2)
    if actual_init2 != expected_init2:
        common.refuse("U0k init_2nd.sh is not the exact checked-in patch of U0j")
    if common.sha_bytes(original_init2.encode()) != patch.get("original_init_2nd_sha256"):
        common.refuse("U0k original init_2nd SHA differs from patch report")
    if common.sha_bytes(actual_init2.encode()) != patch.get("patched_init_2nd_sha256"):
        common.refuse("U0k patched init_2nd SHA differs from patch report")
    if u0k_builder.v2.count_modules(before) != 67 or u0k_builder.v2.count_modules(after) != 67:
        common.refuse("U0k module count changed or is not 67")

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
        description="Flash exact U0k direct-mount isolation recovery"
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
        print(f"REFUSING U0k flash: {exc}", file=sys.stderr)
        raise SystemExit(1)

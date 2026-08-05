#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
U0L_FLASH = HERE / "flash-a33-u0l-openrc-cgroup-isolation.py"
BUILDER = HERE / "make-u0m-watchdog-magic-close-v3.py"
AUDIT = HERE / "audit-a33-u0m-candidate-v3.py"
EXPECTED_U0L_FLASH_BLOB = "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
EXPECTED_BUILDER_BLOB = "1e48bdd42905845046fc95e28e3cd597ae350df1"
EXPECTED_AUDIT_BLOB = "d4b5b3d1ef271b4d02d1ca77592a1c1d8e3bf356"
EXPECTED_RECOVERY_SIZE = 100663296


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


common = load(
    "a33_u0m_v3_flash_common",
    HERE / "flash-a33-u0i-python-direct-root-v2.py",
)
u0l_flash = load("a33_u0m_v3_flash_parent", U0L_FLASH)
builder = load("a33_u0m_v3_flash_builder", BUILDER)

sys.path.insert(0, str(HERE / "lib"))
from a33_rootfs_recovery_flash import FlashProfile, execute_flash

PROFILE = FlashProfile(
    operation="flash-exact-u0m-v3-watchdog-magic-close",
    report_name="a33-first-rootfs-u0m-v3-watchdog-magic-close-flash.txt",
    remote_candidate="/tmp/a33x-u0m-v3-watchdog-magic-close-recovery.img",
    success_label="U0m v3 watchdog magic-close",
)


def common_contract() -> dict[str, str]:
    return {
        "implementation_language": "python3",
        "functional_base": "U0l-openrc-cgroup-isolation",
        "cpio_entry_order_preserved": "yes",
        "cpio_payload_delta": "hooks/01-a33x-watchdog.sh,init_2nd.sh",
        "shell_delta": "driver-log-verified-watchdog-magic-close-before-switch-root",
        "watchdog_device": "/dev/watchdog0",
        "watchdog_magic_close_byte": "V",
        "watchdog_failure_behavior": "continue-feeding-and-refuse-switch-root",
        "watchdog_config_source": "/proc/config.gz",
        "watchdog_config_gz_sha256": builder.inspector.EXPECTED_CONFIG_SHA256,
        "watchdog_config_nowayout": "explicitly-not-set",
        "watchdog_config_handle_boot_enabled": "y",
        "watchdog_config_open_timeout": "0",
        "watchdog_config_s3c2410_watchdog": "m",
        "watchdog_runtime_parameter_required": "no",
        "watchdog_class_state_required": "no",
        "watchdog_stop_verification": (
            "driver-stop-log-increment-and-no-did-not-stop-increment"
        ),
        "watchdog_stop_log": builder.base.STOP_LOG,
        "watchdog_did_not_stop_log": builder.base.DID_NOT_STOP_LOG,
        "rootfs_persistent_delta": "none",
        "runtime_mount_delta": "retain-u0l-openrc-cgroup-mask",
        "embedded_modules": "67",
        "kernel_cmdline_delta": "none",
        "module_delta": "none",
        "kernel_delta": "none",
        "dtb_delta": "none",
        "recovery_dtbo_delta": "none",
        "userdata_write": "none",
        "phone_partition_writes": "no",
    }


def manifest_contract() -> dict[str, str]:
    return {
        "candidate": "U0m-watchdog-magic-close",
        "functional_delta": (
            "host-pinned-nowayout-disabled-and-driver-log-verified-"
            "magic-close-before-switch-root"
        ),
        **common_contract(),
        "preparation_status": "passed",
        "build_status": "passed",
    }


def patch_contract() -> dict[str, str]:
    return {
        "operation": "python-u0m-v3-host-config-pinned-watchdog-magic-close",
        **common_contract(),
        "patch_status": "passed",
    }


def audit_contract() -> dict[str, str]:
    return {
        "operation": "host-only-audit-u0m-v3-exact-delta",
        "functional_base": "U0l-openrc-cgroup-isolation",
        "initramfs_payload_delta": "watchdog-hook-and-init_2nd-only",
        "recovery_component_delta": "ramdisk-and-avb-authentication-only",
        "recovery_dtbo_offset_formula_verified": "yes",
        "watchdog_config_identity_pinned": "yes",
        "watchdog_nowayout_explicitly_disabled": "yes",
        "watchdog_runtime_parameter_required": "no",
        "watchdog_class_state_required": "no",
        "watchdog_driver_stop_log_required": "yes",
        "watchdog_magic_close_contract": "passed",
        "watchdog_fail_closed_contract": "passed",
        "kernel_unchanged": "yes",
        "dtb_unchanged": "yes",
        "recovery_dtbo_unchanged": "yes",
        "kernel_cmdline_unchanged": "yes",
        "recovery_size_exact": "yes",
        "rootfs_persistent_delta": "none",
        "phone_partition_writes": "no",
        "audit_status": "passed",
    }


def require_sha(value: str, label: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        common.refuse(f"invalid SHA256 in U0m v3 {label}: {value!r}")


def run_host_audit(root: Path, repo: Path) -> Path:
    report = root / "build/a33-u0m-v3-candidate-audit.txt"
    console = root / "build/a33-u0m-v3-flash-preflight-audit-console.txt"
    completed = subprocess.run(
        [
            sys.executable,
            str(AUDIT),
            "--root",
            str(root),
            "--repo",
            str(repo),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    console.parent.mkdir(parents=True, exist_ok=True)
    console.write_text(
        completed.stdout
        + ("\n=== stderr ===\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        common.refuse(
            "U0m v3 host audit failed before flash: "
            f"rc={completed.returncode} console={console}\n{completed.stderr.strip()}"
        )
    if not report.is_file() or not report.read_bytes():
        common.refuse(f"U0m v3 audit produced no report: {report}")
    return report


def validate_generated_evidence(root: Path) -> dict[str, object]:
    manifest_path = (
        root / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-manifest.txt"
    )
    patch_path = root / "build/u0m-watchdog-magic-close-patch.txt"
    audit_path = root / "build/a33-u0m-v3-candidate-audit.txt"
    candidate = root / "build/candidates/a33x-h1-usbpd-u0m-watchdog-magic-close-recovery.img"
    initramfs = root / "export-u0m-watchdog-magic-close/initramfs"
    contract_report = root / "build/a33-watchdog-kernel-contract.txt"
    for path in (
        manifest_path,
        patch_path,
        audit_path,
        candidate,
        initramfs,
        contract_report,
    ):
        if not path.is_file():
            common.refuse(f"missing U0m v3 preflight evidence: {path}")

    manifest = common.kv(manifest_path)
    patch = common.kv(patch_path)
    audit = common.kv(audit_path)
    common.require(manifest, manifest_contract(), "U0m v3 manifest")
    common.require(patch, patch_contract(), "U0m v3 patch report")
    common.require(audit, audit_contract(), "U0m v3 candidate audit")

    if Path(manifest.get("recovery", "")).resolve() != candidate.resolve():
        common.refuse("U0m v3 manifest references an unexpected recovery")
    if Path(manifest.get("u0m_initramfs", "")).resolve() != initramfs.resolve():
        common.refuse("U0m v3 manifest references an unexpected initramfs")
    if Path(audit.get("manifest", "")).resolve() != manifest_path.resolve():
        common.refuse("U0m v3 audit references an unexpected manifest")
    if Path(audit.get("patch_report", "")).resolve() != patch_path.resolve():
        common.refuse("U0m v3 audit references an unexpected patch report")
    if Path(manifest.get("watchdog_config_contract_report", "")).resolve() != contract_report.resolve():
        common.refuse("U0m v3 references an unexpected watchdog contract report")

    try:
        candidate_size = int(manifest.get("recovery_size", ""))
        audit_size = int(audit.get("candidate_size", ""))
    except ValueError:
        common.refuse("invalid U0m v3 candidate size")
    candidate_sha = manifest.get("recovery_sha256", "")
    require_sha(candidate_sha, "candidate")
    if candidate_size != EXPECTED_RECOVERY_SIZE or audit_size != candidate_size:
        common.refuse("unexpected U0m v3 recovery size")
    if audit.get("candidate_sha256") != candidate_sha:
        common.refuse("U0m v3 audit and manifest disagree on recovery SHA256")
    if candidate.stat().st_size != candidate_size or common.sha_file(candidate) != candidate_sha:
        common.refuse("U0m v3 recovery differs from its evidence")

    initramfs_sha = manifest.get("u0m_initramfs_sha256", "")
    require_sha(initramfs_sha, "initramfs")
    if common.sha_file(initramfs) != initramfs_sha:
        common.refuse("U0m v3 initramfs differs from its manifest")
    if patch.get("u0m_initramfs_sha256") != initramfs_sha:
        common.refuse("U0m v3 patch and manifest disagree on initramfs")

    manifest_sha = common.sha_file(manifest_path)
    patch_sha = common.sha_file(patch_path)
    if audit.get("manifest_sha256") != manifest_sha:
        common.refuse("U0m v3 manifest changed after audit")
    if audit.get("patch_report_sha256") != patch_sha:
        common.refuse("U0m v3 patch changed after audit")
    if manifest.get("patch_report_sha256") != patch_sha:
        common.refuse("U0m v3 manifest and patch identity disagree")
    if manifest.get("watchdog_config_contract_report_sha256") != common.sha_file(
        contract_report
    ):
        common.refuse("watchdog contract report changed after build")

    for key in (
        "u0l_manifest_sha256",
        "u0l_initramfs_sha256",
        "u0m_initramfs_sha256",
        "original_watchdog_hook_sha256",
        "patched_watchdog_hook_sha256",
        "original_init_2nd_sha256",
        "patched_init_2nd_sha256",
        "watchdog_config_gz_sha256",
        "watchdog_config_contract_report_sha256",
    ):
        require_sha(manifest.get(key, ""), key)
        if patch.get(key) != manifest.get(key):
            common.refuse(f"U0m v3 patch and manifest disagree on {key}")

    return {
        "manifest_path": manifest_path,
        "manifest": manifest,
        "patch_path": patch_path,
        "audit_path": audit_path,
        "candidate": candidate,
        "candidate_sha": candidate_sha,
        "candidate_size": candidate_size,
    }


def validate_local(root: Path, repo: Path) -> dict[str, object]:
    root = root.expanduser().resolve()
    repo = repo.expanduser().resolve()
    for path, expected in (
        (U0L_FLASH, EXPECTED_U0L_FLASH_BLOB),
        (BUILDER, EXPECTED_BUILDER_BLOB),
        (AUDIT, EXPECTED_AUDIT_BLOB),
    ):
        if builder.base.u0l.u0j.git_blob(repo, path) != expected:
            common.refuse(f"checked-in U0m v3 flash dependency changed: {path.name}")

    parent = u0l_flash.validate_local(root, repo)
    run_host_audit(root, repo)
    evidence = validate_generated_evidence(root)
    manifest = evidence["manifest"]
    parent_manifest = Path(parent["manifest_path"])
    if Path(str(manifest["u0l_manifest"])).resolve() != parent_manifest.resolve():
        common.refuse("U0m v3 does not descend from validated U0l")
    if common.sha_file(parent_manifest) != manifest["u0l_manifest_sha256"]:
        common.refuse("validated U0l manifest differs from U0m v3 ancestry")
    parent.update(evidence)
    return parent


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flash exact host-audited U0m v3 recovery"
    )
    parser.add_argument("--root", type=Path, default=Path.home() / "a33-port")
    parser.add_argument("--repo", type=Path, default=Path.home() / "Linuxa33")
    parser.add_argument("--adb", default=os.environ.get("ADB", "adb"))
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    root = args.root.expanduser().resolve()
    repo = args.repo.expanduser().resolve()
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
        print(f"REFUSING U0m v3 flash: {exc}", file=sys.stderr)
        raise SystemExit(1)

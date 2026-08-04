#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
spec = importlib.util.spec_from_file_location(
    "u0l_flash", HERE / "flash-a33-u0l-openrc-cgroup-isolation.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.PROFILE.validate()
assert module.PROFILE.operation == "flash-exact-u0l-openrc-cgroup-isolation"
assert (
    module.PROFILE.report_name
    == "a33-first-rootfs-u0l-openrc-cgroup-isolation-flash.txt"
)
assert (
    module.PROFILE.remote_candidate
    == "/tmp/a33x-u0l-openrc-cgroup-isolation-recovery.img"
)
assert module.manifest_contract()["rootfs_persistent_delta"] == "none"
assert module.manifest_contract()["module_delta"] == "none"
assert module.patch_contract()["patch_status"] == "passed"
assert module.audit_contract()["audit_status"] == "passed"
assert (
    module.audit_contract()[
        "boot_header_unchanged_except_ramdisk_size_and_recovery_dtbo_offset"
    ]
    == "yes"
)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    build = root / "build"
    candidates = build / "candidates"
    candidates.mkdir(parents=True)
    candidate = candidates / "a33x-h1-usbpd-u0l-openrc-cgroup-isolation-recovery.img"
    initramfs = root / "export-u0l-openrc-cgroup-isolation/initramfs"
    initramfs.parent.mkdir(parents=True)
    candidate.write_bytes(b"u0l-candidate-fixture")
    initramfs.write_bytes(b"u0l-initramfs-fixture")

    previous_size = module.EXPECTED_RECOVERY_SIZE
    module.EXPECTED_RECOVERY_SIZE = candidate.stat().st_size
    try:
        hash_fields = {
            "u0k_manifest_sha256": "1" * 64,
            "u0k_initramfs_sha256": "2" * 64,
            "u0l_initramfs_sha256": module.common.sha_file(initramfs),
            "original_init_2nd_sha256": "3" * 64,
            "patched_init_2nd_sha256": "4" * 64,
            "openrc_cgroup_target_sha256": "5" * 64,
        }
        patch_path = build / "u0l-openrc-cgroup-isolation-patch.txt"
        patch_values = {**module.patch_contract(), **hash_fields}
        patch_path.write_text(
            "".join(f"{key}={value}\n" for key, value in patch_values.items()),
            encoding="utf-8",
        )
        patch_sha = module.common.sha_file(patch_path)

        manifest_path = (
            candidates / "a33x-h1-usbpd-u0l-openrc-cgroup-isolation-manifest.txt"
        )
        manifest_values = {
            **module.manifest_contract(),
            **hash_fields,
            "u0k_manifest": str(build / "u0k-manifest.txt"),
            "u0l_initramfs": str(initramfs),
            "patch_report": str(patch_path),
            "patch_report_sha256": patch_sha,
            "recovery": str(candidate),
            "recovery_size": str(candidate.stat().st_size),
            "recovery_sha256": module.common.sha_file(candidate),
        }
        manifest_path.write_text(
            "".join(f"{key}={value}\n" for key, value in manifest_values.items()),
            encoding="utf-8",
        )
        manifest_sha = module.common.sha_file(manifest_path)

        audit_path = build / "a33-u0l-candidate-audit.txt"
        audit_values = {
            **module.audit_contract(),
            "candidate": str(candidate),
            "candidate_size": str(candidate.stat().st_size),
            "candidate_sha256": module.common.sha_file(candidate),
            "manifest": str(manifest_path),
            "manifest_sha256": manifest_sha,
            "patch_report": str(patch_path),
            "patch_report_sha256": patch_sha,
        }
        audit_path.write_text(
            "".join(f"{key}={value}\n" for key, value in audit_values.items()),
            encoding="utf-8",
        )

        evidence = module.validate_generated_evidence(root)
        assert evidence["candidate"] == candidate
        assert evidence["candidate_sha"] == module.common.sha_file(candidate)
        assert evidence["manifest_path"] == manifest_path
        assert evidence["audit_path"] == audit_path

        candidate.write_bytes(b"tampered")
        try:
            module.validate_generated_evidence(root)
        except module.common.Refusal:
            pass
        else:
            raise AssertionError("tampered U0l candidate was accepted")
    finally:
        module.EXPECTED_RECOVERY_SIZE = previous_size

try:
    module.FlashProfile(
        operation="bad operation",
        report_name="../bad.txt",
        remote_candidate="/data/bad.img",
        success_label="bad\nlabel",
    ).validate()
except ValueError:
    pass
else:
    raise AssertionError("unsafe U0l flash profile was accepted")


class DummyCommon:
    RECOVERY = "/dev/block/by-name/recovery"

    @staticmethod
    def sha_file(path: Path) -> str:
        return {
            Path("/tmp/deploy.txt"): "d" * 64,
            Path("/tmp/manifest.txt"): "m" * 64,
        }[path]


local = {
    "deploy_path": Path("/tmp/deploy.txt"),
    "root_uuid": "7b056328-bdfb-496b-ac38-2624c43c863a",
    "critical_manifest_sha": "c" * 64,
    "manifest_path": Path("/tmp/manifest.txt"),
    "candidate_size": 100663296,
    "candidate_sha": "9" * 64,
}
pairs = dict(
    module.execute_flash.__globals__["report_pairs"](
        DummyCommon,
        module.PROFILE,
        local,
        Path("/tmp/candidate.img"),
        "9" * 64,
        "2026-08-04T21:00:00+02:00",
    )
)
assert pairs["operation"] == module.PROFILE.operation
assert pairs["candidate_sha256"] == "9" * 64
assert pairs["recovery_partition_sha256"] == "9" * 64
assert pairs["userdata_written"] == "no"
assert pairs["cache_written"] == "no"
assert pairs["super_written"] == "no"
assert pairs["boot_written"] == "no"
assert pairs["recovery_written"] == "yes"
assert pairs["reboot_performed"] == "no"

print("u0l_flash_profile_self_test=passed")
print("u0l_manifest_patch_audit_contracts=passed")
print("exact_candidate_evidence_fixture=passed")
print("tampered_candidate_refusal=passed")
print("unsafe_profile_refusal=passed")
print("shared_flash_report_contract=passed")

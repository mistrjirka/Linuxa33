#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

spec = importlib.util.spec_from_file_location(
    "u0j_flash", HERE / "flash-a33-u0j-root-api-compatible.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

module.PROFILE.validate()
assert module.PROFILE.operation == "flash-exact-u0j-root-api-compatible"
assert module.PROFILE.report_name == "a33-first-rootfs-u0j-root-api-compatible-flash.txt"
assert module.PROFILE.remote_candidate == "/tmp/a33x-u0j-root-api-compatible-recovery.img"

manifest = module.manifest_contract()
assert manifest["candidate"] == "U0j-root-api-compatible"
assert manifest["shell_delta"] == "find_root_partition"
assert manifest["wait_root_function_preserved"] == "yes"
assert manifest["find_root_stdout_api"] == "passed"
assert manifest["find_root_output_variable_api"] == "partition"
assert manifest["caller_local_partition_contract"] == "passed"

patch = module.patch_contract()
assert patch["operation"] == "python-byte-preserving-fix-find-root-dual-api"
assert patch["find_root_stdout_call_count"] == "4"
assert patch["find_root_output_variable_call_count"] == "3"
assert patch["find_root_output_variable_consumers"] == module.EXPECTED_CONSUMERS

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
    raise AssertionError("unsafe flash profile was accepted")

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
        "2026-08-04T14:00:00+02:00",
    )
)
assert pairs["operation"] == module.PROFILE.operation
assert pairs["candidate_sha256"] == "9" * 64
assert pairs["recovery_partition_sha256"] == "9" * 64
assert pairs["userdata_written"] == "no"
assert pairs["recovery_written"] == "yes"
assert pairs["reboot_performed"] == "no"

print("u0j_flash_profile_self_test=passed")
print("u0j_manifest_contract=passed")
print("u0j_patch_contract=passed")
print("unsafe_profile_refusal=passed")
print("shared_flash_report_contract=passed")

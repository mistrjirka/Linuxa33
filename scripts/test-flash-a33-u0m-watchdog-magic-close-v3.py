#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0m-watchdog-magic-close-v3.py"
spec = importlib.util.spec_from_file_location("a33_u0m_v3_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.PROFILE.validate()
assert module.PROFILE.operation == "flash-exact-u0m-v3-watchdog-magic-close"
assert module.PROFILE.report_name == (
    "a33-first-rootfs-u0m-v3-watchdog-magic-close-flash.txt"
)
assert module.manifest_contract()["watchdog_config_nowayout"] == "explicitly-not-set"
assert module.manifest_contract()["watchdog_runtime_parameter_required"] == "no"
assert module.manifest_contract()["watchdog_class_state_required"] == "no"
assert module.manifest_contract()["watchdog_stop_verification"] == (
    "driver-stop-log-increment-and-no-did-not-stop-increment"
)
assert module.patch_contract()["patch_status"] == "passed"
assert module.audit_contract()["audit_status"] == "passed"
assert module.audit_contract()["watchdog_config_identity_pinned"] == "yes"
assert module.audit_contract()["watchdog_driver_stop_log_required"] == "yes"

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
        "2026-08-05T10:30:00+02:00",
    )
)
assert pairs["candidate_sha256"] == "9" * 64
assert pairs["recovery_partition_sha256"] == "9" * 64
assert pairs["userdata_written"] == "no"
assert pairs["cache_written"] == "no"
assert pairs["super_written"] == "no"
assert pairs["boot_written"] == "no"
assert pairs["recovery_written"] == "yes"
assert pairs["reboot_performed"] == "no"

assert module.EXPECTED_BUILDER_BLOB == "1e48bdd42905845046fc95e28e3cd597ae350df1"
assert module.EXPECTED_AUDIT_BLOB == "d4b5b3d1ef271b4d02d1ca77592a1c1d8e3bf356"
print("u0m_v3_flash_profile_self_test=passed")
print("host_config_and_driver_log_contract=passed")
print("recovery_only_write_contract=passed")
print("no_automatic_reboot_contract=passed")
print("builder_and_audit_identity_pinned=passed")

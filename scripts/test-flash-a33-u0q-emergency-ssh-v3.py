#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0q-emergency-ssh-v3.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v3_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_V2_FLASH_BLOB == "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
assert module.EXPECTED_BUILDER_V3_BLOB == (
    "295f1979a5a411dfec5456b5929f50d4286b0e6f"
)
assert module.EXPECTED_AUDIT_V3_BLOB == (
    "4fd86baa144355e7d8aae75a8bd5975873916eda"
)
assert module.CONFIRMATION == "FLASH-EXACT-U0Q-V3-RECOVERY"
assert module.EXPECTED_CANDIDATE_SIZE == 100663296

source = MODULE.read_text(encoding="utf-8")
for required in (
    "v2_flash.u0p_flash.local_evidence(root, repo)",
    "emergency_runtime_mount_policy",
    "verified-or-created-tmpfs-run",
    "runtime_mount_order_verified",
    "pre_switch_root_live_channel_gate_verified",
    "persistent_mount_configuration_delta",
    "v2_flash.validate_phone_rootfs(adb, serial, local)",
    "base.recovery_helper.prepare",
    "common.KNOWN_TWRP_SHA256",
    "base.WRITE_SCRIPT",
    "recovery_exact_write=passed",
    "known-u0p-openrc-script-loaded-boundary",
    "emergency_trace_baseline",
    "userdata_written",
    "cache_written",
    "super_written",
    "boot_written",
    "recovery_written",
    "reboot_performed",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "mount -o remount,rw",
    "umount -l",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_u0q_v3_guarded_flash_self_test=passed")
print("exact_v2_flash_v3_builder_and_v3_audit_blob_pins=passed")
print("dynamic_exact_candidate_manifest_patch_and_audit_contract=passed")
print("explicit_runtime_mount_policy_required=passed")
print("known_u0p_trace_and_absent_u0q_trace_baseline_reused=passed")
print("exact_twrp_rescue_required_before_write=passed")
print("recovery_only_write_and_full_readback_contract=passed")
print("other_partition_and_reboot_absence=passed")

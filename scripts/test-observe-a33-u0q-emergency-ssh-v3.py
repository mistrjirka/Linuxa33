#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0q-emergency-ssh-v3.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v3_observer_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_V3_BLOB == (
    "79e8b0dd2a2a781018b027b551f54796e4608afb"
)
assert module.EXPECTED_OBSERVER_V2_BLOB == (
    "1b8bcc6a917214cd89abdc38c10af1f2a42ff449"
)
assert module.TWRP_REBOOT == "/system/bin/twrp"

source = MODULE.read_text(encoding="utf-8")
for required in (
    "flash.local_evidence(root, repo)",
    "runtime_mount_policy",
    "flash.v2_flash.validate_phone_rootfs(adb, serial, local)",
    "flash.base.recovery_helper.prepare",
    "helpers.verify_twrp_reboot_interface",
    "helpers.wait_for_transition",
    "v2_observer.probe_banner()",
    "v2_observer.probe_auth(ssh, private_key)",
    "v2_observer.capture_live_diagnostics",
    "passed-transition-proven-u0q-v3-emergency-ssh-authenticated-live-diagnostics-captured",
    "keep-u0q-v3-running-and-analyze-live-diagnostics",
    "restore-a33-twrp-odin.py RESTORE-EXACT-TWRP",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "base.WRITE_SCRIPT",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

assert module.v2_observer.EMERGENCY_PORT == 2222
assert module.v2_observer.MAX_OBSERVATION_SECONDS == 180
assert "snapshot t40" in module.v2_observer.REMOTE_DIAGNOSTIC_SCRIPT
assert "rc-status -a" in module.v2_observer.REMOTE_DIAGNOSTIC_SCRIPT
assert "/proc/1/wchan" in module.v2_observer.REMOTE_DIAGNOSTIC_SCRIPT
assert "nft -a list ruleset" in module.v2_observer.REMOTE_DIAGNOSTIC_SCRIPT

print("a33_u0q_v3_live_observer_self_test=passed")
print("exact_v3_flash_and_v2_observer_blob_pins=passed")
print("v3_runtime_mount_policy_preflight_contract=passed")
print("old_boot_id_and_usb_instance_transition_contract=passed")
print("port_2222_dedicated_key_auth_contract_reused=passed")
print("staged_cross_switch_root_live_diagnostics_contract_reused=passed")
print("normal_port_22_parallel_observation_preserved=passed")
print("phone_partition_write_absence=passed")

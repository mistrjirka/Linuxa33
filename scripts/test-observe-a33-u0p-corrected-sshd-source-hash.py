#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0p-corrected-sshd-source-hash.py"

spec = importlib.util.spec_from_file_location("a33_u0p_observer_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "793b82e81247654c7a2eb7200e130df56268fd83"
assert module.EXPECTED_U0O_OBSERVER_V2_BLOB == "4231f2f2c71d5ebfc008b9c5da156e4ffd392b9f"
assert module.OBSERVATION_SECONDS == 90
assert module.TWRP_REBOOT == "/system/bin/twrp"
assert module.flash.EXPECTED_CANDIDATE_SHA256 == "59f22a3d27eb63cd8d616e7e55e0ecd16fe91a16fbe8e68759d724d2405d5264"
assert module.flash.CORRECTED_SSHD_SHA256 == "52ddad2085f6364b8a94f21dfd1d092f24c808a43b2fd28c16386c284bf94ea6"

source = MODULE.read_text(encoding="utf-8")
for required in (
    "flash.local_evidence(root, repo)",
    "flash.validate_phone_rootfs(adb, serial, local)",
    "flash.base.recovery_helper.prepare",
    "flash.EXPECTED_CANDIDATE_SHA256",
    "known-u0o-instrumented-source-hash-mismatch",
    "persistent_trace_baseline_sha256",
    "helpers.verify_twrp_reboot_interface(adb, serial)",
    "helpers.require_single_usb_line(lsusb_cmd)",
    "helpers.wait_for_transition(",
    "reboot_transition_verified",
    "old_usb_line",
    "old_boot_id",
    "while True:",
    "if elapsed >= OBSERVATION_SECONDS:",
    "passed-transition-proven-full-90-second-window",
    "enter-download-mode-and-restore-exact-twrp-immediately",
):
    assert required in source, required

# Reuse of U0o observer v2 provides timeout-safe ADB boot-ID classification.
assert module.helpers.adb_boot_id is module.u0o_observer_v2.adb_boot_id
assert module.helpers.TRANSITION_TIMEOUT_SECONDS == 45
assert "if row[\"ssh_banner\"]:\n                break" not in source

for forbidden in (
    "dd if=",
    "mkfs",
    "wipefs",
    "mount -o remount,rw",
    "fastboot",
    "odin4 -a",
):
    assert forbidden not in source, forbidden

assert '[adb, "-s", serial, "shell", TWRP_REBOOT, "reboot", "recovery"]' in source

print("a33_u0p_observer_self_test=passed")
print("exact_u0p_flash_and_observer_blob_pins=passed")
print("known_u0o_failure_trace_preboot_contract=passed")
print("timeout_safe_old_boot_id_and_usb_instance_transition_contract=passed")
print("full_90_second_no_early_exit_contract=passed")
print("exact_twrp_restore_next_action_contract=passed")
print("phone_partition_write_absence=passed")

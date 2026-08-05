#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0o-persistent-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0o_observer_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "441f3c055ca25aa06cd195f1f28b78365817949c"
assert module.EXPECTED_U0N_OBSERVER_BLOB == "31b6f288ddeb743afb8b338b08c7169dbfe4f31e"
assert module.EXPECTED_REBOOT_PROBE_BLOB == "3fffa9475aaa4bcf0f9654727efb429a2cafdaaf"
assert module.TWRP_REBOOT == "/system/bin/twrp"
assert module.OBSERVATION_SECONDS == 90
assert module.TRANSITION_TIMEOUT_SECONDS == 45

old_boot = "old-boot-id"
old_usb = "Bus 003 Device 022: ID 04e8:6860 Samsung"
original_adb_boot_id = module.adb_boot_id
original_usb_lines = module.usb_lines
try:
    module.adb_boot_id = lambda adb, serial: old_boot
    module.usb_lines = lambda lsusb: [old_usb]
    unchanged = module.transition_sample(
        "adb", "serial", "lsusb", old_boot, old_usb, 0.5
    )
    assert unchanged["old_adb_boot_gone"] is False
    assert unchanged["old_usb_instance_gone"] is False

    module.adb_boot_id = lambda adb, serial: ""
    module.usb_lines = lambda lsusb: [
        "Bus 003 Device 023: ID 04e8:6860 Samsung"
    ]
    transitioned = module.transition_sample(
        "adb", "serial", "lsusb", old_boot, old_usb, 1.5
    )
    assert transitioned["old_adb_boot_gone"] is True
    assert transitioned["old_usb_instance_gone"] is True
finally:
    module.adb_boot_id = original_adb_boot_id
    module.usb_lines = original_usb_lines

source = MODULE.read_text(encoding="utf-8")
for required in (
    '[adb, "-s", serial, "shell", TWRP_REBOOT, "reboot", "recovery"]',
    "current_boot_id != old_boot_id",
    "old_usb_line not in current_usb",
    'if row["old_adb_boot_gone"] and row["old_usb_instance_gone"]:',
    "if consecutive >= 2:",
    "failed-old-twrp-adb-or-usb-instance-never-disappeared",
    "passed-transition-proven-full-90-second-window",
    "if elapsed >= OBSERVATION_SECONDS:",
    "persistent_trace_path",
    "enter-download-mode-and-restore-exact-twrp-immediately",
):
    assert required in source, required

# The observer may only invoke the exact TWRP-native reboot command. It must not
# use the ineffective generic adb reboot path and must not stop on SSH success.
for forbidden in (
    'common.run([adb, "-s", serial, "reboot", "recovery"])',
    '"adb", "reboot", "recovery"',
    'if row["ssh_banner"]:\n                break',
    "dd if=",
    "mkfs",
    "wipefs",
    "mount -o remount,rw",
    "fastboot",
    "odin4 -a",
):
    assert forbidden not in source, forbidden

print("a33_u0o_observer_self_test=passed")
print("exact_twrp_native_reboot_command_contract=passed")
print("old_boot_id_and_usb_instance_dual_transition_contract=passed")
print("consecutive_transition_sample_requirement=passed")
print("full_90_second_no_early_exit_contract=passed")
print("persistent_trace_and_exact_twrp_next_action_contract=passed")
print("phone_partition_write_absence=passed")

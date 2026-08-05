#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-installed-ssh-v3.py"

spec = importlib.util.spec_from_file_location("a33_installed_ssh_v3_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "ed5f4050809305171fa2e85a868249ee28e2b633"
assert module.EXPECTED_CLEANUP_BLOB == "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
assert module.EXACT_USERDATA == "/dev/block/sda36"
assert module.EXPECTED_USERDATA_BYTES == "114240258048"
assert module.EXPECTED_UUID == "7b056328-bdfb-496b-ac38-2624c43c863a"
assert module.EXPECTED_LABEL == "pmOS_root"
assert module.base.base.EXPECTED_USERDATA == module.EXACT_USERDATA
assert module.common.EXPECTED_USERDATA == module.EXACT_USERDATA

assert module.recovery_gate("") == "runtime-fingerprint-recovery-path-unreadable"
assert module.recovery_gate(module.EXPECTED_TWRP_SHA256) == (
    "recovery-partition-sha256-and-runtime-fingerprint"
)
try:
    module.recovery_gate("0" * 64)
except module.InspectionV3Error:
    pass
else:
    raise AssertionError("readable mismatching recovery hash was accepted")

values = {
    "userdata_resolved": module.EXACT_USERDATA,
    "userdata_bytes": module.EXPECTED_USERDATA_BYTES,
    "userdata_readonly": "0",
}
module.assert_userdata_idle(
    values,
    {"mount_users": [], "swap_users": [], "dm_users": []},
)
try:
    module.assert_userdata_idle(
        values,
        {"mount_users": ["/tmp/leak"], "swap_users": [], "dm_users": []},
    )
except module.InspectionV3Error:
    pass
else:
    raise AssertionError("active userdata mount was accepted")

remote_script = module.base.base.REMOTE_SCRIPT
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in remote_script
assert "readonly_mount=passed" in remote_script
assert "readonly_unmount=passed" in remote_script

source = MODULE.read_text(encoding="utf-8")
for required in (
    "common.USERDATA = EXACT_USERDATA",
    "cleanup.validate_runtime_fingerprint",
    '"userdata_persistent_writes": "no"',
    '"phone_partition_writes": "no"',
    '"recovery_written": "no"',
    '"phone_reboot_performed": "no"',
):
    assert required in source, required
for forbidden in (
    "/dev/block/by-name/userdata",
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
    "fastboot",
):
    assert forbidden not in source, forbidden

print("a33_installed_ssh_v3_self_test=passed")
print("exact_userdata_node_contract=passed")
print("v2_inspector_and_cleanup_blob_pins=passed")
print("twrp_runtime_fallback_gate=passed")
print("userdata_idle_before_after_contract=passed")
print("read_only_inspection_contract_preserved=passed")
print("phone_write_flash_and_reboot_absence=passed")

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "validate-a33-installed-rootfs-read-only.py"

spec = importlib.util.spec_from_file_location("a33_installed_rootfs_readonly_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "a4523f358e853026279bc780feeb3c5306c2ea29"
assert module.EXPECTED_CLEANUP_BLOB == "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
assert module.recovery_gate("") == "runtime-fingerprint-recovery-path-unreadable"
assert module.recovery_gate(module.EXPECTED_TWRP_SHA256) == (
    "recovery-partition-sha256-and-runtime-fingerprint"
)
try:
    module.recovery_gate("0" * 64)
except module.ValidationError:
    pass
else:
    raise AssertionError("mismatching readable recovery hash was accepted")

fixture = """
critical_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa path=/bin/busybox
critical_sha=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb path=/usr/sbin/sshd
readonly_verification=passed
readonly_unmount=passed
"""
assert module.parse_critical_hashes(fixture) == {
    "/bin/busybox": "a" * 64,
    "/usr/sbin/sshd": "b" * 64,
}

values = {
    "userdata_resolved": module.common.EXPECTED_USERDATA,
    "userdata_bytes": str(module.common.EXPECTED_USERDATA_BYTES),
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
except module.ValidationError:
    pass
else:
    raise AssertionError("active userdata mount was accepted")

verify_script = module.common.VERIFY_SCRIPT
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in verify_script
assert "readonly_verification=passed" in verify_script
assert "readonly_unmount=passed" in verify_script
for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=\"$1\" of=\"$2\"",
    "mkfs",
    "wipefs",
):
    assert forbidden not in verify_script

source = MODULE.read_text(encoding="utf-8")
for forbidden in (
    "execute_flash(",
    "adb reboot",
    "odin4",
    "fastboot",
    "recovery_written\": \"yes",
):
    assert forbidden not in source
assert '"userdata_persistent_writes": "no"' in source
assert '"recovery_written": "no"' in source
assert '"phone_reboot_performed": "no"' in source

print("a33_installed_rootfs_readonly_validator_self_test=passed")
print("u0m_and_cleanup_dependency_pins=passed")
print("twrp_runtime_fallback_gate=passed")
print("readable_recovery_mismatch_refusal=passed")
print("critical_hash_parser=passed")
print("userdata_idle_before_after_contract=passed")
print("read_only_mount_and_unmount_contract=passed")
print("phone_write_flash_and_reboot_absence=passed")

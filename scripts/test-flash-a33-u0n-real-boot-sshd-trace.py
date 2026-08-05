#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0n-real-boot-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0n_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.CONFIRMATION == "FLASH-EXACT-U0N-RECOVERY"
assert module.EXPECTED_CANDIDATE_SHA256 == (
    "9196109cba6a6e13f314b2aba28de21580c8b434c74e075c451d84b48da1bc2d"
)
assert module.EXPECTED_CANDIDATE_SIZE == 100663296
assert module.EXPECTED_MANIFEST_SHA256 == (
    "ee9c238ba3d509c8216ce4457f20cfaa6eecf7dfc38feba03c4343a0641d20df"
)
assert module.EXPECTED_PATCH_SHA256 == (
    "cf9eef5628f6d4a81197a5f82162afcdcb87c02cf0652a43b52f3cfa1e1bc7a7"
)
assert len(module.EXPECTED_KEYS) == 8
assert sum(1 for kind, _, _ in module.EXPECTED_KEYS.values() if kind == "private") == 4
assert sum(1 for kind, _, _ in module.EXPECTED_KEYS.values() if kind == "public") == 4
assert module.recovery_helper.PARTNAME == "recovery"
assert module.recovery_helper.TEMP_NODE == "/tmp/a33x-exact-recovery.block"

for script in (module.KEY_CHECK_SCRIPT, module.WRITE_SCRIPT):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "remote.sh"
        path.write_text(script, encoding="utf-8")
        subprocess.run(["sh", "-n", str(path)], check=True)

key_script = module.KEY_CHECK_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "host_key_verified",
    "host_key_private_count=$private_count",
    "host_key_public_count=$public_count",
    "sshd_pam_binary=present-executable",
    "sshd_default_runlevel=enabled",
    "readonly_key_preflight_unmount=passed",
    "userdata_persistent_writes=no",
):
    assert required in key_script, required
for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "rm -rf",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in key_script, forbidden

write_script = module.WRITE_SCRIPT
for required in (
    'dd if="$source" of="$target" bs=4194304 count=24',
    'sha256sum "$target"',
    "recovery_exact_write=passed",
    "recovery_written=yes",
    "userdata_written=no",
    "cache_written=no",
    "super_written=no",
    "boot_written=no",
    "phone_reboot_performed=no",
):
    assert required in write_script, required
for forbidden in (
    "/dev/block/by-name/recovery",
    "/dev/block/by-name/userdata",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
    "fastboot",
):
    assert forbidden not in write_script, forbidden

source = MODULE.read_text(encoding="utf-8")
for required in (
    "restore.local_evidence(root, repo)",
    "rescue.verify_assets",
    "validate_phone_rootfs(adb, serial, local)",
    "recovery_helper.prepare",
    "recovery_helper.cleanup",
    "exact_twrp_recovery_partition=passed",
    "u0n_flash_preflight_status=passed",
    "identity-critical-hashes-and-exact-host-keys-passed",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "umount -l",
    "mount -o remount,rw",
    "mkfs",
    "wipefs",
    "fastboot",
):
    assert forbidden not in source, forbidden

print("a33_u0n_guarded_flash_self_test=passed")
print("exact_candidate_manifest_patch_audit_contract=passed")
print("exact_rootfs_and_eight_host_key_preflight=passed")
print("sysfs_recovery_target_and_current_twrp_hash_contract=passed")
print("twrp_rescue_assets_required_before_write=passed")
print("explicit_recovery_write_confirmation_contract=passed")
print("recovery_only_write_and_readback_contract=passed")
print("other_partition_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "restore-a33-rootfs-after-unsafe-openrc-diagnostic.py"

spec = importlib.util.spec_from_file_location("a33_rootfs_restore_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.CONFIRMATION == "RESTORE-EXACT-A33-ROOTFS"
assert module.EXPECTED_IMAGE_SIZE == 802160640
assert module.READBACK_MIB == 765
assert module.EXPECTED_IMAGE_SHA256 == (
    "79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951"
)
assert module.EXPECTED_UUID == "7b056328-bdfb-496b-ac38-2624c43c863a"
assert module.EXPECTED_LABEL == "pmOS_root"
assert module.block_helper.EXACT_NODE == "/dev/block/sda36"
assert module.block_helper.EXACT_BYTES == "114240258048"

assert module.EXPECTED_FLASH_BLOB == "a4523f358e853026279bc780feeb3c5306c2ea29"
assert module.EXPECTED_CLEANUP_BLOB == "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
assert module.EXPECTED_BLOCK_HELPER_BLOB == "2232f92bbf2782aed88acd9246ed063148ca63a8"
assert module.EXPECTED_IDENTITY_HELPER_BLOB == "547aa185c56cfdefe09efab2ba1fbe1e63950de0"
assert module.EXPECTED_VERIFY_HELPER_BLOB == "3968d9b2a439ac222b652a79306e611d23525579"

fixture = """
critical_sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa path=/bin/busybox
critical_sha=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb path=/usr/sbin/sshd
"""
assert module.parse_critical_hashes(fixture) == {
    "/bin/busybox": "a" * 64,
    "/usr/sbin/sshd": "b" * 64,
}

safe_values = {
    "userdata_resolved": module.block_helper.EXACT_NODE,
    "userdata_bytes": module.block_helper.EXACT_BYTES,
    "userdata_readonly": "0",
}
module.assert_idle(
    safe_values,
    {"mount_users": [], "swap_users": [], "dm_users": []},
)
try:
    module.assert_idle(
        safe_values,
        {"mount_users": ["/tmp/leak"], "swap_users": [], "dm_users": []},
    )
except module.RestoreError:
    pass
else:
    raise AssertionError("active userdata mount was accepted")

for script in (module.DAMAGE_SCRIPT, module.WRITE_SCRIPT):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "remote.sh"
        path.write_text(script, encoding="utf-8")
        subprocess.run(["sh", "-n", str(path)], check=True)

for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "required_missing_path_state=missing path=$path",
    "surviving_mountpoint_state=directory path=$path",
    "damage_signature=unsafe-chroot-rm-rf-after-failed-unmount",
    "readonly_damage_unmount=passed",
    "phone_partition_writes=no",
):
    assert required in module.DAMAGE_SCRIPT, required

for required in (
    'dd if="$source" of="$target" bs=1048576 count="$readback_mib"',
    'dd if="$target" bs=1048576 count="$readback_mib"',
    "userdata_exact_prefix_write=passed",
    "phone_partition_writes=yes-userdata-exact-image-only",
    "recovery_written=no",
    "boot_written=no",
    "super_written=no",
):
    assert required in module.WRITE_SCRIPT, required

source = MODULE.read_text(encoding="utf-8")
for required in (
    "flash.base.validate_local(root, repo)",
    "cleanup.validate_runtime_fingerprint(adb, serial)",
    "block_helper.prepare(common, adb, serial)",
    "block_helper.cleanup(common, adb, serial, state)",
    "identity_helper.ext4_identity(common, adb, serial)",
    "verify_helper.ROOTFS_SAFE_VERIFY_SCRIPT",
    "remote_staged = False",
    "RESTORE CLEANUP WARNING",
    "ssh_host_keys_after_restore",
):
    assert required in source, required

for forbidden in (
    "/dev/block/by-name/userdata",
    "adb reboot",
    "odin4",
    "fastboot",
    "cache_written\": \"yes",
    "super_written\": \"yes",
    "boot_written\": \"yes",
    "recovery_written\": \"yes",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_rootfs_restore_self_test=passed")
print("exact_image_hash_size_uuid_label_contract=passed")
print("explicit_destructive_confirmation_contract=passed")
print("damage_signature_gate=passed")
print("userdata_only_exact_prefix_write_contract=passed")
print("full_phone_side_readback_sha_contract=passed")
print("postwrite_identity_and_critical_hash_verification=passed")
print("temporary_stage_and_block_node_cleanup_finally=passed")
print("other_partition_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

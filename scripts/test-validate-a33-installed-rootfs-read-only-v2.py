#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "validate-a33-installed-rootfs-read-only-v2.py"

spec = importlib.util.spec_from_file_location("a33_installed_rootfs_readonly_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "d3c15477af1bb53e0890637f16eafc865a2d0368"
assert module.EXPECTED_HELPER_BLOB == "2232f92bbf2782aed88acd9246ed063148ca63a8"
assert module.EXPECTED_EXT4_IDENTITY_BLOB == "547aa185c56cfdefe09efab2ba1fbe1e63950de0"
assert module.EXACT_USERDATA == "/dev/block/sda36"
assert module.base.common.EXPECTED_USERDATA == module.EXACT_USERDATA
assert module.helper.EXACT_NODE == module.EXACT_USERDATA
module.configure_exact_userdata()
assert module.base.common.USERDATA == module.EXACT_USERDATA
assert callable(module.base.common.ext4_identity)

assert module.adb_argument([]) == "adb"
assert module.adb_argument(["--adb", "/tmp/adb"]) == "/tmp/adb"
assert module.adb_argument(["--adb=/tmp/adb2"]) == "/tmp/adb2"

live_script = module.base.common.LIVE_SCRIPT
assert 'target="$1"' in live_script
assert 'resolved="$(readlink -f "$target"' in live_script
assert 'blockdev --getsize64 "$target"' in live_script
assert 'blockdev --getro "$target"' in live_script

verify_script = module.base.common.VERIFY_SCRIPT
assert 'mount -t ext4 -o ro,noload,nosuid,nodev,noatime "$target"' in verify_script
assert "readonly_verification=passed" in verify_script
assert "readonly_unmount=passed" in verify_script

source = MODULE.read_text(encoding="utf-8")
for required in (
    "helper.prepare(base.common, adb, serial)",
    "helper.cleanup(base.common, adb, serial, state)",
    "ext4_identity.ext4_identity",
    "ext4_identity_transport=adb-shell-base64",
    "exact_block_node_cleanup=passed",
    "ephemeral_device_node_write=/dev-tmpfs-only",
):
    assert required in source, required
for forbidden in (
    "/dev/block/by-name/userdata",
    "execute_flash(",
    "adb reboot",
    "odin4",
    "fastboot",
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
):
    assert forbidden not in source

print("a33_installed_rootfs_readonly_v2_self_test=passed")
print("exact_userdata_node_reconstruction=passed")
print("base_block_and_identity_helper_blobs_pinned=passed")
print("text_safe_ext4_identity_override=passed")
print("cleanup_finally_contract=passed")
print("live_state_direct_target_contract=passed")
print("read_only_verify_contract_preserved=passed")
print("phone_partition_write_flash_and_reboot_absence=passed")

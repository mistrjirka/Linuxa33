#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-installed-ssh-v4.py"

spec = importlib.util.spec_from_file_location("a33_installed_ssh_v4_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "78a7e93678f34cb2a038a76b7bf8716bb6b6a64c"
assert module.EXPECTED_HELPER_BLOB == "2232f92bbf2782aed88acd9246ed063148ca63a8"
assert module.helper.EXACT_NODE == module.base.EXACT_USERDATA
assert module.adb_argument([]) == "adb"
assert module.adb_argument(["--adb", "/tmp/adb"]) == "/tmp/adb"
assert module.adb_argument(["--adb=/tmp/adb2"]) == "/tmp/adb2"

source = MODULE.read_text(encoding="utf-8")
for required in (
    "helper.prepare(common, adb, serial)",
    "helper.cleanup(common, adb, serial, state)",
    "exact_block_node_cleanup=passed",
    "ephemeral_device_node_write=/dev-tmpfs-only",
    "return base.main()",
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
    assert forbidden not in source

remote_script = module.base.base.base.REMOTE_SCRIPT
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in remote_script
assert "readonly_mount=passed" in remote_script
assert "readonly_unmount=passed" in remote_script

print("a33_installed_ssh_v4_self_test=passed")
print("v3_inspector_and_exact_node_helper_pinned=passed")
print("ephemeral_node_prepare_and_finally_cleanup=passed")
print("read_only_inspection_contract_preserved=passed")
print("phone_partition_write_and_reboot_absence=passed")

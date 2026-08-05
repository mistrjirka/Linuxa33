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
assert module.EXACT_USERDATA == "/dev/block/sda36"
assert module.base.base.EXPECTED_USERDATA == module.EXACT_USERDATA
assert module.base.base.common.EXPECTED_USERDATA == module.EXACT_USERDATA
module.configure_exact_userdata()
assert module.base.base.common.USERDATA == module.EXACT_USERDATA

remote_script = module.base.base.REMOTE_SCRIPT
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in remote_script
assert "readonly_root_mount=passed" in remote_script
assert "readonly_unmount=passed" in remote_script
assert "phone_partition_writes=no" in remote_script

source = MODULE.read_text(encoding="utf-8")
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

print("a33_installed_ssh_v3_self_test=passed")
print("exact_userdata_node_override=passed")
print("v2_inspector_blob_identity_pinned=passed")
print("read_only_inspection_contract_preserved=passed")
print("phone_write_and_reboot_absence=passed")

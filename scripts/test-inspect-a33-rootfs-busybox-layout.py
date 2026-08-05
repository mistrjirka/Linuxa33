#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-rootfs-busybox-layout.py"

spec = importlib.util.spec_from_file_location("a33_busybox_layout_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_COMMON_BLOB == "84b47e4f75d2d9622e1fd081000f1e387d7dd6cd"
assert module.EXPECTED_BLOCK_HELPER_BLOB == "2232f92bbf2782aed88acd9246ed063148ca63a8"
assert module.EXPECTED_IDENTITY_HELPER_BLOB == "547aa185c56cfdefe09efab2ba1fbe1e63950de0"
assert module.block_helper.EXACT_NODE == "/dev/block/sda36"

fixture = """
layout path=/bin type=symlink target=usr/bin
layout path=/bin/busybox type=missing
layout path=/usr/bin/busybox type=file mode=755 bytes=123 sha256=abc
busybox_find_begin
/usr/bin/busybox
busybox_find_end
"""
layout = module.parse_layout(fixture)
assert layout["/bin"]["type"] == "symlink"
assert layout["/bin"]["target"] == "usr/bin"
assert layout["/usr/bin/busybox"]["type"] == "file"
found = module.section(fixture, "busybox_find")
assert found == ["/usr/bin/busybox"]
assert module.diagnose(layout, found) == (
    "usr-bin-busybox-present-bin-symlink-resolution-mismatch"
)
assert module.diagnose(
    {"/bin/busybox": {"type": "file"}}, []
) == "bin-busybox-present-verifier-runtime-mismatch"
assert module.diagnose({}, []) == "busybox-not-found-in-mounted-rootfs"

script = module.REMOTE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "inspect_path",
    "/bin/busybox",
    "/usr/bin/busybox",
    "busybox_find_begin",
    "readonly_unmount=passed",
    "userdata_persistent_writes=no",
    "phone_partition_writes=no",
    "phone_reboot_performed=no",
):
    assert required in script, required
for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
    "fastboot",
):
    assert forbidden not in script

source = MODULE.read_text(encoding="utf-8")
for required in (
    "block_helper.prepare(common, adb, serial)",
    "identity_helper.ext4_identity(common, adb, serial)",
    "block_helper.cleanup(common, adb, serial, state)",
    "exact_block_node_cleanup=passed",
):
    assert required in source, required

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "remote.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_rootfs_busybox_layout_self_test=passed")
print("exact_block_node_and_ext4_identity_pins=passed")
print("bin_and_usr_busybox_layout_capture=passed")
print("layout_diagnosis_classification=passed")
print("read_only_mount_and_cleanup_contract=passed")
print("phone_partition_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

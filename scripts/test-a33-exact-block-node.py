#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "lib/a33_exact_block_node.py"

spec = importlib.util.spec_from_file_location("a33_exact_block_node_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = """
exact_node=/dev/block/sda36
exact_node_resolved=/dev/block/sda36
exact_node_created=yes
exact_parent_created=no
exact_sysfs=/sys/class/block/sda36
exact_sysfs_resolved=/sys/devices/platform/ufs/block/sda/sda36
exact_kernel_name=sda36
exact_kernel_dev=8:36
exact_node_hex_dev=8:24
exact_node_bytes=114240258048
exact_node_readonly=0
exact_block_node_status=passed
"""
state = module.state_from_output(fixture)
assert state.node == module.EXACT_NODE
assert state.created is True
assert state.parent_created is False
assert state.kernel_name == module.EXACT_KERNEL_NAME
assert state.kernel_dev == "8:36"
assert state.bytes == module.EXACT_BYTES

for script in (module.PREPARE_SCRIPT, module.CLEANUP_SCRIPT):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "remote.sh"
        path.write_text(script, encoding="utf-8")
        subprocess.run(["sh", "-n", str(path)], check=True)

assert 'mknod "$node" b "$major" "$minor"' in module.PREPARE_SCRIPT
assert 'blockdev --getsize64 "$node"' in module.PREPARE_SCRIPT
assert 'readlink -f "/sys/dev/block/$kernel_dev"' in module.PREPARE_SCRIPT
assert 'rm -f "$node"' in module.CLEANUP_SCRIPT
assert "mount " not in module.PREPARE_SCRIPT
assert "mount " not in module.CLEANUP_SCRIPT
for forbidden in (
    "dd if=",
    "mkfs",
    "wipefs",
    "flash_image",
    "odin4",
    "fastboot",
    "adb reboot",
):
    assert forbidden not in MODULE.read_text(encoding="utf-8")


class FakeCommon:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def adb_shell(self, adb: str, serial: str, script: str, *args: str) -> str:
        self.calls.append((adb, serial, *args))
        if script == module.PREPARE_SCRIPT:
            return fixture
        if script == module.CLEANUP_SCRIPT:
            return "exact_block_node_cleanup_status=passed\n"
        raise AssertionError("unexpected remote script")


fake = FakeCommon()
with module.exact_block_node(fake, "adb", "SERIAL") as active:
    assert active.created is True
assert len(fake.calls) == 2
assert fake.calls[0][2] == module.EXACT_NODE
assert fake.calls[1][2] == module.EXACT_NODE

fake_exception = FakeCommon()
try:
    with module.exact_block_node(fake_exception, "adb", "SERIAL"):
        raise RuntimeError("fixture failure")
except RuntimeError as exc:
    assert str(exc) == "fixture failure"
else:
    raise AssertionError("context exception was not propagated")
assert len(fake_exception.calls) == 2

print("a33_exact_block_node_self_test=passed")
print("sysfs_major_minor_identity_contract=passed")
print("exact_size_and_kernel_name_contract=passed")
print("ephemeral_mknod_only_contract=passed")
print("cleanup_on_success_and_exception=passed")
print("phone_partition_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

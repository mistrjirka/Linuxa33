#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "lib/a33_exact_recovery_node.py"

spec = importlib.util.spec_from_file_location("a33_exact_recovery_node_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.PARTNAME == "recovery"
assert module.EXPECTED_BYTES == "100663296"
assert module.TEMP_NODE == "/tmp/a33x-exact-recovery.block"

fixture = """
exact_recovery_node=/tmp/a33x-exact-recovery.block
exact_recovery_node_created=yes
exact_recovery_partname=recovery
exact_recovery_kernel_name=sda18
exact_recovery_kernel_dev=259:2
exact_recovery_sysfs=/sys/class/block/sda18
exact_recovery_sysfs_resolved=/sys/devices/platform/ufs/sda/sda18
exact_recovery_node_hex_dev=103:2
exact_recovery_bytes=100663296
exact_recovery_readonly=0
exact_recovery_sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
exact_recovery_active_users=none
exact_recovery_node_status=passed
"""
state = module.state_from_output(fixture, "a" * 64)
assert state.created
assert state.kernel_name == "sda18"
assert state.kernel_dev == "259:2"
assert state.sha256 == "a" * 64

for script in (module.PREPARE_SCRIPT, module.CLEANUP_SCRIPT):
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "remote.sh"
        path.write_text(script, encoding="utf-8")
        subprocess.run(["sh", "-n", str(path)], check=True)

prepare = module.PREPARE_SCRIPT
for required in (
    'grep -Fqx "PARTNAME=$partname"',
    'match_count=$((match_count + 1))',
    '[ "$match_count" -eq 1 ]',
    'kernel_dev="$(cat "$sysfs/dev"',
    'mknod "$node" b "$major" "$minor"',
    'blockdev --getsize64 "$node"',
    'blockdev --getro "$node"',
    'sha256sum "$node"',
    'exact_recovery_active_users=none',
    'exact_recovery_node_status=passed',
):
    assert required in prepare, required
for forbidden in (
    "/dev/block/by-name/recovery",
    "mount -o remount,rw",
    "umount -l",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
    "fastboot",
):
    assert forbidden not in prepare, forbidden

cleanup = module.CLEANUP_SCRIPT
assert 'rm -f "$node"' in cleanup
assert "exact_recovery_node_cleanup_status=passed" in cleanup
assert "rm -rf" not in cleanup

print("a33_exact_recovery_node_self_test=passed")
print("sysfs_partname_unique_match_contract=passed")
print("major_minor_size_and_full_sha_contract=passed")
print("active_mount_swap_dm_refusal_contract=passed")
print("temporary_node_cleanup_contract=passed")
print("partition_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

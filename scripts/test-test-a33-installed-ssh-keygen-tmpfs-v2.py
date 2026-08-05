#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "test-a33-installed-ssh-keygen-tmpfs-v2.py"

spec = importlib.util.spec_from_file_location("a33_ssh_keygen_tmpfs_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

script = module.base.REMOTE_SCRIPT
assert module.OLD_REQUIRED_BLOCK not in script
assert script.count("rootfs_required_path()") == 1
assert script.count('if ! rootfs_required_path "$required"; then') == 1
assert "resolution=symlink" in script
assert "rootfs_required_symlink_broken" in script
assert "target_full=\"$root$target_relative\"" in script
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in script
assert "mount -t tmpfs -o mode=0755,size=16m" in script
assert "userdata_persistent_writes=no" in script
assert "phone_partition_writes=no" in script

for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "sed -i",
):
    assert forbidden not in script

with tempfile.TemporaryDirectory() as temp:
    shell_path = Path(temp) / "remote.sh"
    shell_path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(shell_path)], check=True)

assert module.EXPECTED_BASE_BLOB == "7f4b5df59f4ce1c42a2235c037ae667e554c3087"

print("a33_installed_ssh_keygen_tmpfs_v2_self_test=passed")
print("rootfs_absolute_symlink_resolution=passed")
print("direct_required_path_validation=passed")
print("read_only_rootfs_contract_preserved=passed")
print("volatile_tmpfs_overlay_contract_preserved=passed")
print("persistent_write_absence=passed")
print("shell_syntax_validation=passed")

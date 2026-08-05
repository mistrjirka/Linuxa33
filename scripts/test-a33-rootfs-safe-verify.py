#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "lib/a33_rootfs_safe_verify.py"
spec = importlib.util.spec_from_file_location("a33_rootfs_safe_verify_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

script = module.ROOTFS_SAFE_VERIFY_SCRIPT
for required in (
    "rootfs_resolve()",
    'case "$link_target" in',
    'resolved="${resolved%/*}"',
    'critical_resolution path=$path',
    "rootfs_path_resolution=rootfs-relative-symlink-safe",
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "readonly_verification=passed",
    "readonly_unmount=passed",
    "phone_partition_writes=no",
):
    assert required in script, required
for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "sed -i",
    "adb reboot",
    "odin4",
):
    assert forbidden not in script

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary) / "root"
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/lib/openrc").mkdir(parents=True)
    (root / "usr/bin/busybox").write_text("busybox\n", encoding="utf-8")
    (root / "usr/lib/openrc/init").write_text("init\n", encoding="utf-8")
    (root / "bin").symlink_to("/usr/bin")
    (root / "sbin").symlink_to("usr/sbin")
    (root / "usr/sbin").mkdir()
    (root / "usr/sbin/init").symlink_to("../lib/openrc/init")

    start = script.index("rootfs_resolve()")
    end = script.index('\n\nfor path in "$@"; do', start)
    function_text = script[start:end]
    fixture = Path(temporary) / "resolve.sh"
    fixture.write_text(
        "set -eu\n"
        f"mountpoint={str(root)!r}\n"
        + function_text
        + "\n"
        + 'rootfs_resolve /bin/busybox\n'
        + 'rootfs_resolve /sbin/init\n',
        encoding="utf-8",
    )
    subprocess.run(["sh", "-n", str(fixture)], check=True)
    completed = subprocess.run(
        ["sh", str(fixture)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert completed.stdout.splitlines() == [
        str(root / "usr/bin/busybox"),
        str(root / "usr/lib/openrc/init"),
    ]

print("a33_rootfs_safe_verify_self_test=passed")
print("absolute_parent_symlink_resolution=passed")
print("relative_parent_and_final_symlink_resolution=passed")
print("rootfs_escape_prevention_contract=passed")
print("critical_hash_original_path_contract=passed")
print("read_only_mount_and_unmount_contract=passed")
print("phone_partition_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

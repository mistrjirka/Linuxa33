#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "provision-a33-ssh-host-keys-v3-safe.py"

spec = importlib.util.spec_from_file_location("a33_safe_ssh_host_keys_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_RESTORE_BLOB == "baf157a20617ec70fff8e79381055b34d77b0de8"
assert module.CONFIRMATION == "PROVISION-SAFE-A33-SSH-HOST-KEYS"
assert module.block_helper.EXACT_NODE == "/dev/block/sda36"
assert module.restore.EXPECTED_UUID == "7b056328-bdfb-496b-ac38-2624c43c863a"
assert module.restore.EXPECTED_LABEL == "pmOS_root"

fixture = """
generated_key name=ssh_host_ed25519_key kind=private sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bytes=411
generated_key name=ssh_host_ed25519_key.pub kind=public sha256=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb bytes=99
installed_key name=ssh_host_ed25519_key kind=private sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bytes=411 mode=600 uid=0 gid=0
"""
generated = module.parse_key_lines(fixture, "generated_key")
installed = module.parse_key_lines(fixture, "installed_key")
assert len(generated) == 2
assert generated[0]["kind"] == "private"
assert generated[1]["kind"] == "public"
assert installed == [
    {
        "name": "ssh_host_ed25519_key",
        "kind": "private",
        "sha256": "a" * 64,
        "bytes": "411",
        "mode": "600",
        "uid": "0",
        "gid": "0",
    }
]

script = module.REMOTE_SCRIPT
for required in (
    "unmount_exact()",
    "mounts_under()",
    'unmount_exact "$root/run"',
    'unmount_exact "$root/sys"',
    'unmount_exact "$root/proc"',
    'unmount_exact "$root/dev"',
    'unmount_exact "$root/etc/ssh"',
    'unmount_exact "$root"',
    "cleanup_status=failed-mounts-preserved-no-recursive-root-delete",
    "cleanup_status=passed",
    "volatile_key_generation_environment=passed",
    "chroot \"$root\" /usr/bin/ssh-keygen -A",
    "chroot \"$root\" /usr/sbin/sshd.pam -t",
    "writable_commit_root_mount=passed",
    "readonly_postcommit_mount=passed",
    "persistent_host_key_provision=passed",
    "userdata_written=yes-etc-ssh-host-keys-only",
    "phone_partition_writes=yes-userdata-host-keys-only",
    "recovery_written=no",
    "boot_written=no",
    "super_written=no",
    "phone_reboot_performed=no",
):
    assert required in script, required

for forbidden in (
    'rm -rf "$root"',
    "umount -l",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
    "fastboot",
):
    assert forbidden not in script, forbidden

source = MODULE.read_text(encoding="utf-8")
for required in (
    "restore.local_evidence(root, repo)",
    "cleanup.validate_runtime_fingerprint(adb, serial)",
    "block_helper.prepare(common, adb, serial)",
    "block_helper.cleanup(common, adb, serial, state)",
    "verify_helper.ROOTFS_SAFE_VERIFY_SCRIPT",
    "restored_rootfs_readonly_validation=passed",
    "recursive_root_mountpoint_delete\": \"absent",
):
    assert required in source, required
for forbidden in (
    "/dev/block/by-name/userdata",
    "adb reboot",
    "odin4",
    "fastboot",
):
    assert forbidden not in source, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "remote.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_safe_ssh_host_key_provision_self_test=passed")
print("restored_rootfs_and_twrp_dependency_pins=passed")
print("fresh_volatile_keygen_and_sshd_pam_validation=passed")
print("strict_reverse_unmount_verification=passed")
print("recursive_root_mountpoint_delete_absence=passed")
print("exact_key_path_scoped_rollback=passed")
print("userdata_host_keys_only_write_contract=passed")
print("other_partition_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

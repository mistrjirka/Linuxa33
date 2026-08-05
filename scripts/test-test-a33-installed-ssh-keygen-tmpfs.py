#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "test-a33-installed-ssh-keygen-tmpfs.py"
spec = importlib.util.spec_from_file_location("a33_ssh_keygen_tmpfs_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

success_fixture = '''readonly_root_mount=passed
volatile_ssh_overlay=passed
userdata_persistent_writes=no
entropy_avail_before=256
ssh_keygen_rc=0
ssh_keygen_timed_out=no
ssh_keygen_elapsed_seconds=2
ssh_keygen_output_begin
ssh_keygen_output_end
generated_host_keys_begin
generated_host_key kind=private path=/etc/ssh/ssh_host_ed25519_key bytes=411 mode=600 uid=0 gid=0 sha256=a
generated_host_key kind=public path=/etc/ssh/ssh_host_ed25519_key.pub bytes=99 mode=644 uid=0 gid=0 sha256=b
generated_private_key_count=1
generated_public_key_count=1
generated_host_keys_end
sshd_config_rc=0
sshd_config_timed_out=no
sshd_config_elapsed_seconds=1
sshd_config_output_begin
sshd_config_output_end
entropy_avail_after=256
volatile_tmpfs_only=yes
phone_partition_writes=no
cleanup_unmount=passed
'''
summary = module.summarize(success_fixture)
assert summary["diagnosis"] == "volatile-keygen-and-sshd-config-validation-passed"
assert summary["generated_private_key_count"] == 1
assert summary["generated_public_key_count"] == 1
assert summary["userdata_persistent_writes"] == "no"
assert summary["phone_partition_writes"] == "no"

failure_fixture = success_fixture.replace(
    "ssh_keygen_rc=0\nssh_keygen_timed_out=no",
    "ssh_keygen_rc=1\nssh_keygen_timed_out=no",
).replace(
    "generated_private_key_count=1", "generated_private_key_count=0"
).replace(
    "generated_public_key_count=1", "generated_public_key_count=0"
)
assert module.summarize(failure_fixture)["diagnosis"] == "ssh-keygen-failed"

timeout_fixture = '''readonly_root_mount=passed
volatile_ssh_overlay=passed
userdata_persistent_writes=no
entropy_avail_before=0
ssh_keygen_process_state_begin
file=wchan
wait_for_random_bytes
ssh_keygen_process_state_end
ssh_keygen_rc=124
ssh_keygen_timed_out=yes
ssh_keygen_elapsed_seconds=45
ssh_keygen_output_begin
ssh_keygen_output_end
generated_host_keys_begin
generated_private_key_count=0
generated_public_key_count=0
generated_host_keys_end
sshd_config_rc=not-run-no-private-keys
sshd_config_timed_out=no
sshd_config_elapsed_seconds=0
sshd_config_output_begin
not run
sshd_config_output_end
entropy_avail_after=0
volatile_tmpfs_only=yes
phone_partition_writes=no
cleanup_unmount=passed
'''
assert module.summarize(timeout_fixture)["diagnosis"] == (
    "ssh-keygen-blocked-waiting-for-randomness"
)

for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "sed -i",
    "ssh-keygen -A" + " > /etc/ssh",
):
    assert forbidden not in module.REMOTE_SCRIPT
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in module.REMOTE_SCRIPT
assert "mount -t tmpfs" in module.REMOTE_SCRIPT
assert 'mount -o bind "$overlay" "$root/etc/ssh"' in module.REMOTE_SCRIPT
assert "userdata_persistent_writes=no" in module.REMOTE_SCRIPT
assert "phone_partition_writes=no" in module.REMOTE_SCRIPT
assert "cleanup_unmount=passed" in module.REMOTE_SCRIPT

print("a33_installed_ssh_keygen_tmpfs_self_test=passed")
print("successful_keygen_and_config_parser=passed")
print("keygen_failure_parser=passed")
print("randomness_block_parser=passed")
print("read_only_rootfs_and_tmpfs_overlay_contract=passed")
print("persistent_write_absence=passed")

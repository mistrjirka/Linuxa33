#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-installed-ssh-v2.py"
spec = importlib.util.spec_from_file_location("a33_installed_ssh_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = '''readonly_mount=passed
path_type=file path=/usr/sbin/sshd bytes=100 mode=755 uid=0 gid=0 mtime=1 sha256=a
path_type=file path=/usr/bin/ssh-keygen bytes=100 mode=755 uid=0 gid=0 mtime=1 sha256=b
path_type=file path=/etc/init.d/sshd bytes=100 mode=755 uid=0 gid=0 mtime=1 sha256=c
path_type=directory path=/etc/ssh mode=755 uid=0 gid=0
runlevels_default_begin
sshd
runlevels_default_end
host_keys_begin
host_keys_end
sshd_config_active_directives_begin
sshd_config_active_directives_end
sshd_conf_d_begin
# defaults
sshd_conf_d_end
ssh_parent_permissions_begin
directory path=/etc mode=755 uid=0 gid=0
directory path=/etc/ssh mode=755 uid=0 gid=0
directory path=/var mode=755 uid=0 gid=0
directory path=/var/empty mode=755 uid=0 gid=0
directory path=/run mode=755 uid=0 gid=0
ssh_parent_permissions_end
sshd_keygen_contract_begin
source=/etc/init.d/sshd
25:generate_host_keys() {
30:        ssh-keygen -A
80:start_pre() {
81:    checkconfig
sshd_keygen_contract_end
expected_host_keys_begin
expected_host_key path=/etc/ssh/ssh_host_rsa_key state=missing
expected_host_key path=/etc/ssh/ssh_host_ecdsa_key state=missing
expected_host_key path=/etc/ssh/ssh_host_ed25519_key state=missing
expected_host_keys_end
ssh_targeted_persistent_logs_begin
targeted_log_file=/var/log/messages
10:sshd: no hostkeys available -- exiting.
ssh_targeted_persistent_logs_end
readonly_unmount=passed
'''
summary = module.summarize(fixture)
assert summary["private_host_key_count"] == 0
assert summary["sshd_init_generates_host_keys"]
assert summary["sshd_init_runs_config_validation"]
assert summary["sshd_disable_keygen_effective"] == "default-no"
assert summary["etc_ssh_directory"]["mode"] == "755"
assert len(summary["missing_expected_host_keys"]) == 3
assert summary["targeted_ssh_log_line_count"] == 2
assert summary["ssh_startup_diagnosis"] == (
    "host-key-generation-did-not-complete-before-sshd-listen"
)

assert module.base.REMOTE_SCRIPT.count("ssh_parent_permissions_begin") == 1
assert module.base.REMOTE_SCRIPT.count("sshd_keygen_contract_begin") == 1
assert module.base.REMOTE_SCRIPT.count("expected_host_keys_begin") == 1
assert module.base.REMOTE_SCRIPT.count("ssh_targeted_persistent_logs_begin") == 1
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in module.base.REMOTE_SCRIPT
for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "ssh-keygen -A >",
    "sed -i",
    "rm -f /etc/ssh",
    "dd if=",
    "mkfs",
):
    assert forbidden not in module.base.REMOTE_SCRIPT

print("a33_installed_ssh_v2_self_test=passed")
print("host_key_generation_contract_parser=passed")
print("expected_host_key_state_parser=passed")
print("targeted_persistent_log_extraction=passed")
print("missing_host_key_diagnosis=passed")
print("read_only_noload_contract_preserved=passed")

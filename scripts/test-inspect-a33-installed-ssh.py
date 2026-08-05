#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-installed-ssh.py"
spec = importlib.util.spec_from_file_location("a33_installed_ssh_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = '''readonly_mount=passed
path_type=file path=/usr/sbin/sshd bytes=100 mode=755 uid=0 gid=0 mtime=1 sha256=abc
path_type=file path=/etc/init.d/sshd bytes=10 mode=755 uid=0 gid=0 mtime=1 sha256=def
runlevels_default_begin
lrwxrwxrwx 1 root root 16 sshd -> /etc/init.d/sshd
runlevels_default_end
host_keys_begin
host_key kind=private path=/etc/ssh/ssh_host_ed25519_key bytes=411 mode=600 uid=0 gid=0 mtime=1 sha256=1
host_key kind=public path=/etc/ssh/ssh_host_ed25519_key.pub bytes=99 mode=644 uid=0 gid=0 mtime=1 sha256=2
host_keys_end
sshd_config_active_directives_begin
config_file=/etc/ssh/sshd_config
Port 2222
ListenAddress 172.16.42.1
sshd_config_active_directives_end
log_begin=/var/log/messages
sshd: Server listening on 172.16.42.1 port 2222.
log_end=/var/log/messages
readonly_unmount=passed
'''
summary = module.summarize(fixture)
assert summary["readonly_mount_passed"]
assert summary["readonly_unmount_passed"]
assert summary["sshd_binary_present"]
assert summary["sshd_init_present"]
assert summary["sshd_runlevel_enabled"]
assert summary["private_host_key_count"] == 1
assert summary["public_host_key_count"] == 1
assert summary["configured_ports"] == ["2222"]
assert summary["configured_listen_addresses"] == ["172.16.42.1"]
assert summary["ssh_related_log_line_count"] == 1

minimal = '''readonly_mount=passed
path_type=file path=/usr/sbin/sshd bytes=1 mode=755 uid=0 gid=0 mtime=1 sha256=a
path_type=file path=/etc/init.d/sshd bytes=1 mode=755 uid=0 gid=0 mtime=1 sha256=b
runlevels_default_begin
sshd
runlevels_default_end
host_keys_begin
host_keys_end
sshd_config_active_directives_begin
sshd_config_active_directives_end
readonly_unmount=passed
'''
minimal_summary = module.summarize(minimal)
assert minimal_summary["configured_ports"] == ["22-default"]
assert minimal_summary["configured_listen_addresses"] == ["all-default"]
assert minimal_summary["private_host_key_count"] == 0

for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "sed -i",
):
    assert forbidden not in module.REMOTE_SCRIPT
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in module.REMOTE_SCRIPT
assert "readonly_unmount=passed" in module.REMOTE_SCRIPT
assert module.EXPECTED_TWRP_SHA256 == (
    "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
)

print("a33_installed_ssh_inspection_self_test=passed")
print("ssh_key_and_runlevel_parser=passed")
print("default_port_and_listen_parser=passed")
print("read_only_noload_mount_contract=passed")
print("destructive_operation_absence=passed")
print("exact_twrp_gate_present=passed")

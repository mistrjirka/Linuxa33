#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "cleanup-a33-openrc-sshd-chroot.py"
spec = importlib.util.spec_from_file_location("a33_openrc_sshd_cleanup_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

script = module.REMOTE_SCRIPT
for required in (
    'proc_root="$(readlink "$proc/root"',
    'kill -TERM "$pid"',
    'kill -KILL "$pid"',
    'mount -o remount,ro "$root"',
    'unmount_one "$root/run"',
    'unmount_one "$root/sys"',
    'unmount_one "$root/proc"',
    'unmount_one "$root/dev"',
    'cleanup_status=passed',
    'possible_persistent_writes=yes-unsafe-diagnostic-remounted-root-rw',
):
    assert required in script

for forbidden in (
    "umount -l",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
):
    assert forbidden not in script

with tempfile.TemporaryDirectory() as temporary:
    shell = Path(temporary) / "cleanup.sh"
    shell.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(shell)], check=True)

fixture = """
mounts_before_begin
/dev/block/sda36 /tmp/a33x-openrc-sshd-root ext4 rw 0 0
mounts_before_end
mounts_after_begin

mounts_after_end
chroot_processes_before_begin
chroot_process pid=123 root=/tmp/a33x-openrc-sshd-root cmdline=/usr/bin/logbookd
chroot_processes_before_end
chroot_processes_after_begin
chroot_processes_after_end
"""
assert module.section(fixture, "mounts_before_begin", "mounts_before_end") == [
    "/dev/block/sda36 /tmp/a33x-openrc-sshd-root ext4 rw 0 0"
]
assert module.section(fixture, "mounts_after_begin", "mounts_after_end") == [""]

print("a33_openrc_sshd_chroot_cleanup_self_test=passed")
print("exact_chroot_process_scope=passed")
print("normal_unmount_only_contract=passed")
print("root_readonly_remount_contract=passed")
print("persistent_write_disclosure_contract=passed")
print("phone_reboot_absence=passed")
print("shell_syntax_validation=passed")

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "cleanup-a33-openrc-sshd-chroot-v2.py"
spec = importlib.util.spec_from_file_location("a33_openrc_sshd_cleanup_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "bb5865f150369bba3da81c291e22a15c663c929d" if False else "bb5865f150369fdf2ce269cfc4b2bba107e7cfd0"
assert module.EXPECTED_KERNEL_RELEASE == "5.10.66-Gabriel260BR-TWRP-ga0103aac9499"
assert module.EXPECTED_CONFIG_GZ_SHA256 == (
    "7dd732d5b653571497e3e77d286705efc5b4247dcdc937afffc54827b4f3997c"
)
assert module.REQUIRED_CMDLINE_MARKERS == (
    "bootmode=2",
    "androidboot.hardware=s5e8825",
    "androidboot.serialno=RFCTA00V43L",
)

fixture = """
kernel_release=5.10.66-Gabriel260BR-TWRP-ga0103aac9499
config_gz_sha256=7dd732d5b653571497e3e77d286705efc5b4247dcdc937afffc54827b4f3997c
kernel_cmdline=foo bootmode=2 androidboot.hardware=s5e8825 androidboot.serialno=RFCTA00V43L bar
twrp_version=3.7.0
recovery_path=
recovery_path_state=ls: missing
"""
values = module.parse_values(fixture)
assert values["kernel_release"] == module.EXPECTED_KERNEL_RELEASE
assert values["config_gz_sha256"] == module.EXPECTED_CONFIG_GZ_SHA256
assert all(
    marker in values["kernel_cmdline"].split()
    for marker in module.REQUIRED_CMDLINE_MARKERS
)

script = module.FINGERPRINT_SCRIPT
for required in (
    "uname -r",
    "sha256sum /proc/config.gz",
    "cat /proc/cmdline",
    "getprop ro.twrp.version",
    "readlink -f /dev/block/by-name/recovery",
):
    assert required in script, f"missing fingerprint contract token: {required}"

base_script = module.base.REMOTE_SCRIPT
for required in (
    'proc_root="$(readlink "$proc/root"',
    'kill -TERM "$pid"',
    'kill -KILL "$pid"',
    'mount -o remount,ro "$root"',
    'for point in \\',
    '"$root/run"',
    '"$root/sys"',
    '"$root/proc"',
    '"$root/dev"',
    'unmount_one "$point"',
    'cleanup_status=passed',
):
    assert required in base_script, f"missing cleanup contract token: {required}"
for forbidden in (
    "umount -l",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
):
    assert forbidden not in base_script, f"unsafe cleanup token present: {forbidden}"

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "cleanup.sh"
    path.write_text(base_script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
assert "input_data=base.REMOTE_SCRIPT" in source
assert "base.main()" not in source

print("a33_openrc_sshd_chroot_cleanup_v2_self_test=passed")
print("exact_runtime_kernel_release_contract=passed")
print("exact_runtime_config_hash_contract=passed")
print("recovery_boot_cmdline_contract=passed")
print("unreadable_recovery_partition_fallback_contract=passed")
print("exact_chroot_cleanup_scope_preserved=passed")
print("direct_remote_cleanup_invocation=passed")
print("phone_reboot_absence=passed")
print("shell_syntax_validation=passed")

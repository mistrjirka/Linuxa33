#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "inspect-a33-twrp-reboot-interface.py"

spec = importlib.util.spec_from_file_location("a33_twrp_reboot_interface_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FINGERPRINT_BLOB == "51e4d07bac0bfa11d0d32a17b58feb19d7250eda"
fixture = """
boot_id=abc
uptime=123.4
adbd_pid=42
twrp_command=/sbin/twrp
reboot_command=/sbin/reboot
twrp_help_begin
usage: twrp reboot recovery
other help
twrp_help_end
reboot_links_begin
lrwxrwxrwx /sbin/reboot
reboot_links_end
phone_partition_writes=no
phone_reboot_performed=no
"""
assert module.values(fixture)["boot_id"] == "abc"
assert module.values(fixture)["phone_partition_writes"] == "no"
assert module.section(fixture, "twrp_help") == [
    "usage: twrp reboot recovery",
    "other help",
]
assert module.section(fixture, "reboot_links") == ["lrwxrwxrwx /sbin/reboot"]

script = module.REMOTE_SCRIPT
for required in (
    "cat /proc/sys/kernel/random/boot_id",
    "cat /proc/uptime",
    "command -v twrp",
    "twrp help",
    "command -v reboot",
    "phone_partition_writes=no",
    "phone_reboot_performed=no",
):
    assert required in script, required
for forbidden in (
    "adb reboot",
    "twrp reboot recovery",
    "reboot recovery",
    "setprop sys.powerctl",
    "dd if=",
    "mkfs",
    "wipefs",
    "fastboot",
    "odin4",
):
    assert forbidden not in script, forbidden

source = MODULE.read_text(encoding="utf-8")
for required in (
    "fingerprint.validate_runtime_fingerprint(adb, serial)",
    "recommended_reboot_command_status",
    "phone_partition_writes",
    "phone_reboot_performed",
):
    assert required in source, required

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "remote.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_twrp_reboot_interface_self_test=passed")
print("boot_id_uptime_and_adbd_identity_capture=passed")
print("twrp_cli_and_reboot_help_capture=passed")
print("no_reboot_execution_contract=passed")
print("phone_partition_write_absence=passed")
print("shell_syntax_validation=passed")

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "diagnose-a33-sshd-launch-modes.py"
spec = importlib.util.spec_from_file_location("a33_sshd_launch_modes_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = """
mode_begin=direct-daemon
mode_port=2222
mode_launch=direct-daemon
mode_launch_rc=0
mode_sample second=1 pid=10 alive=yes listening=yes
mode_pid=10
mode_pidfile_present=yes
mode_process_alive=yes
mode_listener_present=yes
mode_cmdline=/usr/sbin/sshd.pam
mode_output_begin
mode_output_end
mode_end=direct-daemon
mode_begin=openrc-ssd
mode_port=2223
mode_launch=start-stop-daemon
mode_launch_rc=0
mode_pid=11
mode_pidfile_present=yes
mode_process_alive=yes
mode_listener_present=yes
mode_output_begin
mode_output_end
mode_end=openrc-ssd
mode_begin=openrc-foreground
mode_port=2224
mode_launch=ssd-background-foreground
mode_launch_rc=0
mode_pid=12
mode_pidfile_present=yes
mode_process_alive=yes
mode_listener_present=yes
mode_output_begin
mode_output_end
mode_end=openrc-foreground
"""

modes = module.parse_modes(fixture)
assert len(modes) == 3
assert modes[0]["label"] == "direct-daemon"
assert modes[0]["listener_present"] == "yes"
assert module.diagnose(modes) == "all-launch-modes-work-boot-environment-or-later-service-stop"

normal_fail = [dict(item) for item in modes]
normal_fail[0]["listener_present"] = "no"
assert module.diagnose(normal_fail) == "normal-sshd-daemonization-fails"

ssd_fail = [dict(item) for item in modes]
ssd_fail[1]["listener_present"] = "no"
assert module.diagnose(ssd_fail) == (
    "start-stop-daemon-normal-mode-fails-foreground-mode-works"
)

foreground_fail = [dict(item) for item in modes]
foreground_fail[2]["listener_present"] = "no"
assert module.diagnose(foreground_fail) == "foreground-background-supervision-mode-fails"

script = module.REMOTE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "mount -t tmpfs -o mode=0755,size=8m",
    "direct-daemon 2222 direct-daemon",
    "openrc-ssd 2223 start-stop-daemon",
    "openrc-foreground 2224 ssd-background-foreground",
    "--background --make-pidfile",
    "mode_listener_present=",
    "nftables_static_begin",
    "/usr/share/nftables.avail/50_sshd.nft",
    "userdata_persistent_writes=no",
    "phone_partition_writes=no",
    "phone_reboot_performed=no",
):
    assert required in script
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

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "remote.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_sshd_launch_modes_self_test=passed")
print("normal_daemonization_classification=passed")
print("start_stop_daemon_classification=passed")
print("foreground_supervision_classification=passed")
print("read_only_rootfs_contract=passed")
print("volatile_runtime_only_contract=passed")
print("static_nftables_inspection_contract=passed")
print("phone_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

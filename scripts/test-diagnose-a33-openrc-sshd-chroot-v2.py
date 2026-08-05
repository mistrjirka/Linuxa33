#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "diagnose-a33-openrc-sshd-chroot-v2.py"
spec = importlib.util.spec_from_file_location("a33_openrc_sshd_chroot_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

script = module.base.REMOTE_SCRIPT
assert module.OLD_REQUIRED_BLOCK not in script
assert script.count("rootfs_required_path()") == 1
assert 'chroot "$root" /etc/init.d/sshd start' in script
assert 'chroot "$root" /etc/init.d/sshd status' in script
assert 'chroot "$root" /etc/init.d/sshd stop' in script
assert 'mount -o bind /dev/null "$root/usr/libexec/rc/sh/rc-cgroup.sh"' in script
assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in script
assert "mount -t tmpfs -o mode=0755,size=8m" in script
assert "port22_listener=" in script
assert "openrc_state_begin" in script
assert "userdata_persistent_writes=no" in script
assert "phone_partition_writes=no" in script
assert "phone_reboot_performed=no" in script

for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "adb reboot",
    "odin4",
):
    assert forbidden not in script

with tempfile.TemporaryDirectory() as temporary:
    shell = Path(temporary) / "remote.sh"
    shell.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(shell)], check=True)

fixture = """
openrc_start_rc=0
openrc_start_timed_out=no
snapshot_begin=after-start
pidfile_present=yes
pid=100
process_alive=yes
process_cmdline=sshd.pam
process_wchan=do_select
port22_listener=yes
openrc_state_begin
openrc_path=/run/openrc/started/sshd symlink_target= contents=
openrc_state_end
process_matches_begin
root 100 sshd.pam
process_matches_end
snapshot_end=after-start
snapshot_begin=after-stop
pidfile_present=no
pid=missing
process_alive=no
process_cmdline=
process_wchan=
port22_listener=no
openrc_state_begin
openrc_state_end
process_matches_begin
process_matches_end
snapshot_end=after-stop
"""
snapshots = module.base.parse_snapshots(fixture)
assert len(snapshots) == 2
assert snapshots[0]["process_alive"] == "yes"
assert snapshots[0]["port22_listener"] == "yes"
assert module.base.diagnose("0", "no", snapshots) == (
    "exact-openrc-sshd-path-works-real-boot-later-stop-or-ordering"
)
assert module.base.diagnose("1", "no", snapshots) == "exact-openrc-sshd-start-failed"
assert module.base.diagnose("124", "yes", snapshots) == (
    "exact-openrc-sshd-start-timed-out"
)

assert module.EXPECTED_BASE_BLOB == "d104487fbe97c1429d6df222b39fbf5a7e18a21c"

print("a33_openrc_sshd_chroot_v2_self_test=passed")
print("rootfs_absolute_symlink_resolution=passed")
print("exact_openrc_start_status_stop_path=passed")
print("u0l_cgroup_mask_contract=passed")
print("pid_listener_and_openrc_state_capture=passed")
print("read_only_rootfs_contract=passed")
print("volatile_runtime_only_contract=passed")
print("phone_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

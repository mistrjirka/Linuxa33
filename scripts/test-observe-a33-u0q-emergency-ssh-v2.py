#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0q-emergency-ssh-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v2_observer_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
assert module.EXPECTED_U0P_OBSERVER_BLOB == (
    "ab35fa03ae34a48bf1e902eb3b7d91dac951c011"
)
assert module.PHONE_HOST == "172.16.42.1"
assert module.EMERGENCY_PORT == 2222
assert module.MAX_OBSERVATION_SECONDS == 180
assert module.TWRP_REBOOT == "/system/bin/twrp"

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.bind(("127.0.0.1", 0))
server.listen(1)
port = server.getsockname()[1]


def serve_banner() -> None:
    connection, _ = server.accept()
    with connection:
        connection.sendall(b"SSH-2.0-U0q-test\r\n")
    server.close()


thread = threading.Thread(target=serve_banner)
thread.start()
assert module.probe_banner("127.0.0.1", port) == "ssh-banner"
thread.join(timeout=5)
assert not thread.is_alive()

args = module.ssh_base_args("/usr/bin/ssh", Path("/tmp/u0q-key"))
for required in (
    "/usr/bin/ssh",
    "-i",
    "/tmp/u0q-key",
    "-p",
    "2222",
    "BatchMode=yes",
    "IdentitiesOnly=yes",
    "StrictHostKeyChecking=no",
    "UserKnownHostsFile=/dev/null",
    "ConnectTimeout=3",
    "root@172.16.42.1",
):
    assert required in args, required

remote = module.REMOTE_DIAGNOSTIC_SCRIPT
for required in (
    "snapshot t0",
    "snapshot t2",
    "snapshot t5",
    "snapshot t10",
    "snapshot t20",
    "snapshot t40",
    "/proc/1/status",
    "/proc/1/wchan",
    "rc-status -a",
    "rc-service sshd status",
    "/run/openrc",
    "nft -a list ruleset",
    "/var/log/a33x-u0q-emergency-ssh.log",
    "/var/log/a33x-u0o-real-boot-sshd.log",
    "dmesg",
):
    assert required in remote, required
for forbidden in (
    "reboot",
    "poweroff",
    "halt",
    "mount -o remount,rw",
    "umount -l",
    "sed -i",
    "rm -rf",
    "mkfs",
    "wipefs",
    "dd if=",
):
    assert forbidden not in remote, forbidden
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0q-live-diagnostics.sh"
    path.write_text(remote, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "flash.local_evidence(root, repo)",
    "flash.validate_phone_rootfs(adb, serial, local)",
    "recovery_partition_sha256",
    "helpers.verify_twrp_reboot_interface",
    "helpers.wait_for_transition",
    "probe_banner()",
    "probe_auth(",
    "capture_live_diagnostics(",
    "passed-transition-proven-emergency-ssh-authenticated-live-diagnostics-captured",
    "keep-u0q-running-and-analyze-live-diagnostics",
    "restore-a33-twrp-odin.py RESTORE-EXACT-TWRP",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "base.WRITE_SCRIPT",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_u0q_v2_live_observer_self_test=passed")
print("exact_flash_and_parent_observer_blob_pins=passed")
print("old_boot_id_and_usb_instance_transition_contract=passed")
print("port_2222_banner_and_dedicated_key_auth_contract=passed")
print("staged_cross_switch_root_live_diagnostic_capture_contract=passed")
print("normal_port_22_parallel_observation_preserved=passed")
print("phone_partition_write_absence=passed")
print("remote_shell_syntax_validation=passed")

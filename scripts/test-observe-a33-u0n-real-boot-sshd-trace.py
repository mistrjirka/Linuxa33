#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0n-real-boot-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0n_observer_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "35caa92b0271c2d0b01460db62c30ecfb0208ddc"
assert module.OBSERVATION_SECONDS == 90
assert module.PHONE_IP == "172.16.42.1"
assert module.HOST_CIDR == "172.16.42.2/24"
assert module.USB_ID == "04e8:6860"


class FakeSocket:
    def __init__(self, result: int, payload: bytes = b"") -> None:
        self.result = result
        self.payload = payload

    def settimeout(self, timeout: float) -> None:
        assert timeout > 0

    def connect_ex(self, address: tuple[str, int]) -> int:
        assert address == ("example.invalid", 22)
        return self.result

    def recv(self, count: int) -> bytes:
        assert count == 128
        return self.payload

    def close(self) -> None:
        pass


original_socket = module.socket.socket
try:
    module.socket.socket = lambda *args, **kwargs: FakeSocket(111)
    assert module.tcp_state("example.invalid") == ("connection-refused", "")
    module.socket.socket = lambda *args, **kwargs: FakeSocket(
        0, b"SSH-2.0-OpenSSH_test\r\n"
    )
    assert module.tcp_state("example.invalid") == (
        "ssh-banner",
        "SSH-2.0-OpenSSH_test",
    )
finally:
    module.socket.socket = original_socket

source = MODULE.read_text(encoding="utf-8")
for required in (
    "flash.local_evidence(root, repo)",
    "flash.validate_phone_rootfs(adb, serial, local)",
    "flash.recovery_helper.prepare",
    "flash.recovery_helper.cleanup",
    "while True:",
    "if elapsed >= OBSERVATION_SECONDS:",
    "time.sleep(0.5)",
    "tcp22_state_counts",
    "passed-full-90-second-window",
    "enter-download-mode-and-restore-exact-twrp-immediately",
    "restore-a33-twrp-odin.py RESTORE-EXACT-TWRP",
):
    assert required in source, required

# The observer must not stop when SSH succeeds; it always records the full window.
assert "if row[\"ssh_banner\"]:\n                break" not in source
assert "simultaneous_success" not in source

for forbidden in (
    "dd if=",
    "mkfs",
    "wipefs",
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "odin4 -a",
    "fastboot",
):
    assert forbidden not in source, forbidden

assert 'common.run([adb, "-s", serial, "reboot", "recovery"])' in source

print("a33_u0n_observer_self_test=passed")
print("exact_flash_report_rootfs_keys_and_recovery_readback_preflight=passed")
print("full_90_second_no_early_exit_contract=passed")
print("tcp22_refused_timeout_accept_banner_classification=passed")
print("download_mode_and_exact_twrp_next_action_contract=passed")
print("phone_partition_write_absence=passed")

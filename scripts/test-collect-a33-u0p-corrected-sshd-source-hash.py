#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0p-corrected-sshd-source-hash.py"

spec = importlib.util.spec_from_file_location("a33_u0p_collector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "793b82e81247654c7a2eb7200e130df56268fd83"
assert module.EXPECTED_OBSERVER_BLOB == "ab35fa03ae34a48bf1e902eb3b7d91dac951c011"
assert module.EXPECTED_U0O_COLLECTOR_BLOB == "8f4554cb01d3965da0085ba9e78f4d76d69c0aeb"
assert module.TRACE_PATH == "/var/log/a33x-u0o-real-boot-sshd.log"
assert module.MAX_TRACE_BYTES == 1048576
assert module.COUNT_PATTERNS["candidate_trace_open_count"] == (
    r"candidate=U0p-corrected-sshd-source-hash stage=trace-open"
)
assert "instrumented_source_hash_mismatch_count" in module.COUNT_PATTERNS

fixture = """
uptime=16.10 source=initramfs level=6 candidate=U0p-corrected-sshd-source-hash stage=trace-open path=/var/log/a33x-u0o-real-boot-sshd.log
uptime=16.10 source=initramfs level=6 stage=setup-begin
uptime=16.12 source=initramfs level=6 stage=setup-success
uptime=16.20 source=initramfs level=6 stage=switch-root-ready
uptime=17.00 source=openrc level=6 event=script-loaded
uptime=18.00 source=openrc level=6 event=snapshot listener=yes alive=yes openrc=started
"""
counts = module.trace_counts(fixture)
assert counts["candidate_trace_open_count"] == 1
assert counts["setup_success_count"] == 1
assert counts["switch_root_ready_count"] == 1
assert counts["openrc_source_count"] == 2
assert counts["listener_yes_count"] == 1
assert counts["alive_yes_count"] == 1
assert counts["openrc_started_count"] == 1
assert counts["instrumented_source_hash_mismatch_count"] == 0

trace_script = module.TRACE_READ_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "trace_state=missing",
    "trace_state=present-regular",
    "trace_base64_begin",
    "trace_base64_end",
    "trace_readonly_unmount=passed",
    "userdata_persistent_writes=no",
):
    assert required in trace_script, required
for forbidden in (
    "mount -o remount,rw",
    "umount -l",
    "dd if=",
    "mkfs",
    "wipefs",
    "rm -rf",
    "> \"$trace\"",
):
    assert forbidden not in trace_script, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0p-trace-read.sh"
    path.write_text(trace_script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "u0p-corrected-sshd-source-hash-observation-*",
    "reboot_transition_verified",
    "passed-transition-proven-full-90-second-window",
    "common.KNOWN_TWRP_SHA256",
    "flash.u0o_flash.u0n_flash_v2.validate_phone_rootfs(adb, serial, local)",
    "TRACE_READ_SCRIPT",
    "MAX_TRACE_BYTES",
    "trace_metadata_valid",
    "u0p-source-hash-correction-failed-at-runtime",
    "u0p-passed-initramfs-setup-but-no-openrc-trace",
    "u0p-persistent-trace-captured-through-openrc",
    "userdata_persistent_writes",
    "phone_partition_writes",
    "phone_reboot_performed",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "mount -o remount,rw",
    "umount -l",
):
    assert forbidden not in source, forbidden

print("a33_u0p_collector_self_test=passed")
print("transition_proven_observation_requirement=passed")
print("exact_twrp_restore_verification_contract=passed")
print("read_only_noload_trace_transport_contract=passed")
print("bounded_trace_size_and_metadata_contract=passed")
print("corrected_u0p_event_classification_contract=passed")
print("phone_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

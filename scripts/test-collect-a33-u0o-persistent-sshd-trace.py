#!/usr/bin/env python3
from __future__ import annotations

import base64
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0o-persistent-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0o_collector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "441f3c055ca25aa06cd195f1f28b78365817949c"
assert module.EXPECTED_OBSERVER_BLOB == "952ce1d03b79f4cb4d29ad83600d2220be727e01"
assert module.EXPECTED_TWRP_RESTORE_BLOB == "70985f243bd3462cbad97c05ad379eda2958e5c7"
assert module.TRACE_PATH == "/var/log/a33x-u0o-real-boot-sshd.log"
assert module.MAX_TRACE_BYTES == 1048576

trace = (
    "uptime=1.0 source=initramfs level=6 candidate=U0o-persistent-sshd-trace stage=trace-open\n"
    "uptime=2.0 source=initramfs level=6 stage=setup-success\n"
    "uptime=3.0 source=openrc level=6 event=start-pre-exit rc=0 listener=no\n"
    "uptime=4.0 source=openrc level=6 event=snapshot tag=t0 alive=yes listener=yes openrc=started\n"
)
encoded = base64.b64encode(trace.encode()).decode()
fixture = f"""
trace_state=present-regular
trace_mode=600
trace_uid=0
trace_gid=0
trace_bytes={len(trace.encode())}
trace_sha256=dummy
trace_base64_begin
{encoded}
trace_base64_end
trace_readonly_unmount=passed
userdata_persistent_writes=no
"""
assert module.values(fixture)["trace_state"] == "present-regular"
assert module.section(fixture, "trace_base64") == [encoded]
counts = module.trace_counts(trace)
assert counts["candidate_trace_open_count"] == 1
assert counts["initramfs_source_count"] == 2
assert counts["openrc_source_count"] == 2
assert counts["setup_success_count"] == 1
assert counts["start_pre_exit_count"] == 1
assert counts["snapshot_count"] == 1
assert counts["listener_yes_count"] == 1
assert counts["alive_yes_count"] == 1
assert counts["openrc_started_count"] == 1

script = module.TRACE_READ_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "trace_state=missing",
    "trace_state=present-not-regular",
    "trace_state=present-regular",
    "trace_base64_begin",
    "trace_base64_end",
    "trace_readonly_unmount=passed",
    "userdata_persistent_writes=no",
):
    assert required in script, required
for forbidden in (
    "rm -rf",
    "mount -o remount,rw",
    "sed -i",
    "dd if=",
    "mkfs",
    "wipefs",
    ": >",
):
    assert forbidden not in script, forbidden

source = MODULE.read_text(encoding="utf-8")
for required in (
    "reboot_transition_verified",
    "passed-transition-proven-full-90-second-window",
    "common.KNOWN_TWRP_SHA256",
    "flash.u0n_flash_v2.validate_phone_rootfs(adb, serial, local)",
    "TRACE_READ_SCRIPT",
    "u0o-did-not-reach-persistent-trace-creation",
    "u0o-persistent-trace-captured",
    "MAX_TRACE_BYTES",
    "userdata_persistent_writes",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "twrp reboot",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "fastboot",
    "odin4 -a",
    f'rm -f "{module.TRACE_PATH}"',
):
    assert forbidden not in source, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "trace-read.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_u0o_collector_self_test=passed")
print("transition_proven_observation_requirement=passed")
print("exact_twrp_restore_verification_contract=passed")
print("read_only_noload_trace_transport_contract=passed")
print("missing_trace_remains_diagnostic_contract=passed")
print("bounded_trace_size_and_metadata_contract=passed")
print("u0o_event_count_contract=passed")
print("phone_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

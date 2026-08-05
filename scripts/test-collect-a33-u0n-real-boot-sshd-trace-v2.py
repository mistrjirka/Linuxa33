#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0n-real-boot-sshd-trace-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0n_collector_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "060b98413c408326843eb1a61df9e7bcc06d5744"
assert module.base.RAMDISK_SIZE_HEX == "0x00aa0cff"
assert module.base.observer.OBSERVATION_SECONDS == 90

fixture = """
<6>a33x-u0n-real-boot-sshd: stage=setup-begin
<6>a33x-u0n-real-boot-sshd: stage=setup-success
<6>a33x-u0n-real-boot-sshd: event=script-loaded
<6>a33x-u0n-real-boot-sshd: event=update-command rc=0 selected=/usr/sbin/sshd.pam
<6>a33x-u0n-real-boot-sshd: event=start-pre-enter
<6>a33x-u0n-real-boot-sshd: event=start-pre-exit rc=0
<6>a33x-u0n-real-boot-sshd: event=monitor-started pid=123 schedule=0,1,2,5,10,20,30,60
<6>a33x-u0n-real-boot-sshd: event=snapshot tag=t5 alive=yes listener=yes openrc=started
<6>a33x-u0n-real-boot-sshd: event=nft tag=t5 line=tcp dport 22 accept
<6>a33x-u0n-real-boot-sshd: event=monitor-complete schedule=0,1,2,5,10,20,30,60
"""
values = module.base.counts(fixture)
assert values["u0n_setup_begin_count"] == 1
assert values["u0n_setup_success_count"] == 1
assert values["u0n_script_loaded_count"] == 1
assert values["u0n_update_command_count"] == 1
assert values["u0n_start_pre_enter_count"] == 1
assert values["u0n_start_pre_exit_count"] == 1
assert values["u0n_monitor_started_count"] == 1
assert values["u0n_monitor_complete_count"] == 1
assert values["u0n_snapshot_count"] == 1
assert values["u0n_nft_count"] == 1
assert values["u0n_listener_yes_count"] == 1
assert values["u0n_alive_yes_count"] == 1
assert values["u0n_openrc_started_count"] == 1
focused = module.base.focused_lines(fixture)
assert len(focused) == 10
assert any("dport 22" in line for line in focused)

source = MODULE.read_text(encoding="utf-8")
assert "dport\\s+22" in source
assert "[[:space:]]" not in source
assert "return base.main()" in source

base_source = module.BASE_PATH.read_text(encoding="utf-8")
for required in (
    "observer.local_preflight(root, repo)",
    "passed-full-90-second-window",
    "common.KNOWN_TWRP_SHA256",
    '"last_kmsg.bin"',
    '"focused-last-kmsg.txt"',
    '"u0n_ramdisk_size_hex"',
    '"phone_partition_writes": "no"',
    '"phone_reboot_performed": "no"',
):
    assert required in base_source, required
for forbidden in (
    "dd if=",
    "mkfs",
    "wipefs",
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "adb reboot",
    "odin4 -a",
    "fastboot",
):
    assert forbidden not in base_source, forbidden

print("a33_u0n_collector_v2_self_test=passed")
print("python_regex_focus_filter_contract=passed")
print("u0n_openrc_pid_listener_nft_event_count_contract=passed")
print("full_90_second_observation_reference_contract=passed")
print("exact_twrp_partition_verification_before_collection=passed")
print("last_kmsg_and_host_evidence_archive_contract=passed")
print("phone_write_and_reboot_absence=passed")

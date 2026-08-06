#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0q-emergency-ssh-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v2_collector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_BLOB == "333036c0bd13e68b17cbb83c0e978dd07ae308a6"
assert module.EXPECTED_OBSERVER_BLOB == "1b8bcc6a917214cd89abdc38c10af1f2a42ff449"
assert module.EXPECTED_U0P_COLLECTOR_BLOB == (
    "cfa03e126794565af9566ce6f9e4675aa5f2ef02"
)
assert module.MAX_TRACE_BYTES == 4 * 1024 * 1024

script = module.TRACE_READ_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "emit_trace emergency",
    "emit_trace inherited",
    "/var/log/a33x-u0q-emergency-ssh.log",
    "/var/log/a33x-u0o-real-boot-sshd.log",
    'echo "${name}_base64_begin"',
    'echo "${name}_base64_end"',
    "trace_readonly_unmount=passed",
    "userdata_persistent_writes=no",
):
    assert required in script, required
assert script.count('echo "${name}_base64_begin"') == 1
assert script.count('echo "${name}_base64_end"') == 1
assert script.count("emit_trace emergency") == 1
assert script.count("emit_trace inherited") == 1
for forbidden in (
    "mount -o remount,rw",
    "umount -l",
    "sed -i",
    "rm -rf",
    "mkfs",
    "wipefs",
    "dd if=",
    "> \"$path\"",
):
    assert forbidden not in script, forbidden
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0q-v2-trace-collector.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

# The shell function emits these expanded framing markers at runtime when called
# with the two exact names above. The decoder consumes the same name-specific
# section labels, so source-level parameterization and runtime framing agree.
for runtime_name in ("emergency", "inherited"):
    fixture = (
        f"{runtime_name}_state=present-regular\n"
        f"{runtime_name}_bytes=0\n"
        f"{runtime_name}_sha256=e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855\n"
        f"{runtime_name}_base64_begin\n"
        f"{runtime_name}_base64_end\n"
    )
    parsed = module.values(fixture)
    assert parsed[f"{runtime_name}_state"] == "present-regular"
    assert module.section(fixture, f"{runtime_name}_base64") == []
    payload, decoded = module.decode_trace(fixture, parsed, runtime_name)
    assert payload == b""
    assert decoded == ""

emergency_fixture = """
source=initramfs candidate=U0q-emergency-ssh stage=trace-open
source=initramfs event=runtime-directory-ready
source=initramfs event=network-helper-spawned
source=emergency-network event=network-helper-started
source=emergency-network event=network-configured
source=emergency-network event=network-ready-marker-written
source=emergency-sshd event=config-test-start
source=emergency-sshd event=config-test-passed
source=initramfs event=sshd-helper-spawned
source=initramfs event=pre-switch-root-wait
source=initramfs event=pre-switch-root-ready
source=emergency-network event=runtime-firewall-rule-added
Server listening on 0.0.0.0 port 2222.
Accepted publickey for root
"""
emergency_counts = module.count_patterns(
    emergency_fixture, module.EMERGENCY_COUNT_PATTERNS
)
for key in (
    "candidate_trace_open_count",
    "runtime_directory_ready_count",
    "network_configured_count",
    "network_ready_marker_count",
    "config_test_passed_count",
    "sshd_helper_spawned_count",
    "pre_switch_root_ready_count",
    "runtime_firewall_rule_added_count",
    "sshd_listening_count",
    "accepted_publickey_count",
):
    assert emergency_counts[key] == 1, (key, emergency_counts[key])
assert emergency_counts["error_count"] == 0

inherited_fixture = """
candidate=U0p-corrected-sshd-source-hash stage=trace-open
stage=setup-success
stage=switch-root-ready
event=script-loaded
"""
inherited_counts = module.count_patterns(
    inherited_fixture, module.INHERITED_COUNT_PATTERNS
)
assert inherited_counts["u0p_candidate_count"] == 1
assert inherited_counts["setup_success_count"] == 1
assert inherited_counts["switch_root_ready_count"] == 1
assert inherited_counts["script_loaded_count"] == 1

source = MODULE.read_text(encoding="utf-8")
for required in (
    "u0q-v2-emergency-ssh-observation-*",
    "reboot_transition_verified",
    "passed-transition-proven-emergency-ssh-authenticated",
    "common.KNOWN_TWRP_SHA256",
    "validate_phone_rootfs",
    "TRACE_READ_SCRIPT",
    "decode_trace(",
    "u0q-live-emergency-ssh-and-diagnostics-succeeded",
    "u0q-live-channel-ready-but-host-authentication-failed",
    "last_kmsg.bin",
    "host-evidence",
    "phone_partition_writes",
    "phone_reboot_performed",
    "userdata_persistent_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "base.WRITE_SCRIPT",
    "mount -o remount,rw",
    "umount -l",
):
    assert forbidden not in source, forbidden

print("a33_u0q_v2_collector_self_test=passed")
print("exact_flash_observer_and_parent_collector_blob_pins=passed")
print("parameterized_source_to_runtime_trace_framing_contract=passed")
print("transition_proven_observation_requirement=passed")
print("exact_twrp_restore_verification_contract=passed")
print("dual_trace_read_only_noload_transport_contract=passed")
print("bounded_trace_size_metadata_and_hash_contract=passed")
print("emergency_and_inherited_event_classification_contract=passed")
print("phone_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

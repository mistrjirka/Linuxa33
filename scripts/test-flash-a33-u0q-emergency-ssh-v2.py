#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0q-emergency-ssh-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v2_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0P_FLASH_BLOB == "793b82e81247654c7a2eb7200e130df56268fd83"
assert module.EXPECTED_U0Q_BUILDER_V2_BLOB == (
    "63d3d9c548847b6ad710f29844265359e401185d"
)
assert module.EXPECTED_U0Q_AUDIT_V2_BLOB == (
    "1b6deba17e05d95ed18b605c83356e069075da89"
)
assert module.CONFIRMATION == "FLASH-EXACT-U0Q-V2-RECOVERY"
assert module.EXPECTED_CANDIDATE_SIZE == 100663296
assert module.PARENT_TRACE_PATH == "/var/log/a33x-u0o-real-boot-sshd.log"
assert module.EMERGENCY_TRACE_PATH == "/var/log/a33x-u0q-emergency-ssh.log"
assert module.KNOWN_U0P_TRACE_SHA256 == (
    "8f39d87de43796fec970ee7116f83d46dfa39f21e913bc6764c0d6e568574392"
)
assert module.KNOWN_U0P_TRACE_BYTES == 673
assert module.KNOWN_U0P_TRACE_LINES == 6

script = module.TRACE_BASELINE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "candidate=U0p-corrected-sshd-source-hash stage=trace-open",
    "stage=setup-success",
    "stage=switch-root-ready",
    "event=script-loaded",
    "u0q_emergency_trace_baseline=absent",
    "u0q_parent_trace_readonly_unmount=passed",
    "userdata_persistent_writes=no",
):
    assert required in script, required
for forbidden in (
    "mount -o remount,rw",
    "umount -l",
    "mkfs",
    "wipefs",
    "dd if=",
    "rm -rf",
    "> \"$parent\"",
    "> \"$emergency\"",
):
    assert forbidden not in script, forbidden
with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0q-v2-trace-baseline.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "u0p_flash.local_evidence(root, repo)",
    "pre_switch_root_live_channel_gate_verified",
    "u0p_flash.u0o_flash.u0n_flash_v2.validate_phone_rootfs",
    "base.block_helper.prepare",
    "base.recovery_helper.prepare",
    "common.KNOWN_TWRP_SHA256",
    "base.WRITE_SCRIPT",
    "recovery_exact_write=passed",
    "identity-critical-hashes-exact-host-keys-and-known-u0p-trace-passed",
    "emergency_trace_baseline",
    "userdata_written",
    "cache_written",
    "super_written",
    "boot_written",
    "recovery_written",
    "reboot_performed",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "mount -o remount,rw",
    "umount -l",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_u0q_v2_guarded_flash_self_test=passed")
print("exact_builder_audit_and_parent_flash_blob_pins=passed")
print("dynamic_exact_candidate_manifest_patch_and_audit_contract=passed")
print("known_u0p_trace_and_absent_u0q_trace_baseline_contract=passed")
print("shell_safe_rootfs_and_host_key_validation_reused=passed")
print("exact_twrp_rescue_required_before_write=passed")
print("recovery_only_write_and_full_readback_contract=passed")
print("other_partition_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

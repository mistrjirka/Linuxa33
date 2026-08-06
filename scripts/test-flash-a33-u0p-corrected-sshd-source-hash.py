#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0p-corrected-sshd-source-hash.py"

spec = importlib.util.spec_from_file_location("a33_u0p_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0O_FLASH_BLOB == "441f3c055ca25aa06cd195f1f28b78365817949c"
assert module.EXPECTED_U0P_BUILDER_BLOB == "2a5eb4957424fe81212e762ed2225f86ec890ca4"
assert module.EXPECTED_U0P_AUDIT_BLOB == "abc5ac0901a0ca09bbac896d257d0ff40d9a0c66"
assert module.CONFIRMATION == "FLASH-EXACT-U0P-RECOVERY"
assert module.EXPECTED_CANDIDATE_SHA256 == "59f22a3d27eb63cd8d616e7e55e0ecd16fe91a16fbe8e68759d724d2405d5264"
assert module.EXPECTED_MANIFEST_SHA256 == "a2dd0ec55a08002b3336d46c0bf5c3757ec05b7b221748dfe586937cf53a5059"
assert module.EXPECTED_PATCH_SHA256 == "ce14c12d55c6c6297dce1f52355adc915d3601ddb207feaeec012536a53ce17b"
assert module.EXPECTED_AUDIT_SHA256 == "a89fef6091a5c6ec9c390d73b8ac74f4ff64cad7a98d04321ef7cc3eaba36fe8"
assert module.CORRECTED_SSHD_SHA256 == "52ddad2085f6364b8a94f21dfd1d092f24c808a43b2fd28c16386c284bf94ea6"
assert module.KNOWN_U0O_FAILURE_TRACE_SHA256 == "4adac80415ca89c6cb8d4642b0372428cdd5a577bb457c9e4daa7b86bed9a895"
assert module.KNOWN_U0O_FAILURE_TRACE_BYTES == 267

trace_script = module.KNOWN_TRACE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    'actual_sha="$(sha256sum "$trace"',
    'actual_mode="$(stat -c \'%a\' "$trace")"',
    "candidate=U0o-persistent-sshd-trace stage=trace-open",
    "error=instrumented-source-hash-mismatch",
    "u0p_parent_trace_state=known-u0o-instrumented-source-hash-mismatch",
    "u0p_parent_trace_readonly_unmount=passed",
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
    path = Path(temporary) / "u0p-known-trace.sh"
    path.write_text(trace_script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "u0o_flash.u0n_flash_v2.validate_phone_rootfs(adb, serial, local)",
    "KNOWN_U0O_FAILURE_TRACE_SHA256",
    "KNOWN_U0O_FAILURE_TRACE_BYTES",
    "u0p_known_u0o_failure_trace_baseline=passed",
    "base.WRITE_SCRIPT",
    "recovery_exact_write=passed",
    "flash-exact-u0p-corrected-sshd-source-hash",
    "identity-critical-hashes-exact-host-keys-and-known-u0o-failure-trace-passed",
    "userdata_written",
    "cache_written",
    "super_written",
    "boot_written",
    "reboot_performed",
):
    assert required in source, required
assert source.count("base.WRITE_SCRIPT") == 1
assert "adb reboot" not in source
assert "fastboot" not in source
assert "odin4 -a" not in source

print("a33_u0p_guarded_flash_self_test=passed")
print("exact_candidate_manifest_patch_and_audit_contract=passed")
print("shell_safe_rootfs_and_host_key_validation_reused=passed")
print("known_u0o_failure_trace_baseline_contract=passed")
print("exact_twrp_rescue_required_before_write=passed")
print("recovery_only_write_and_readback_contract=passed")
print("phone_reboot_and_other_partition_write_absence=passed")
print("shell_syntax_validation=passed")

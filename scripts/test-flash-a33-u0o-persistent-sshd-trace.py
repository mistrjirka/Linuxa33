#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0o-persistent-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0o_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0N_FLASH_V2_BLOB == "337807470888e0d00a6afb40a5a7ce7bcd8875c3"
assert module.EXPECTED_U0O_BUILDER_V2_BLOB == "88cd0b9b3446314c04ad0c4b20583c2e6facf449"
assert module.EXPECTED_U0O_AUDIT_V2_BLOB == "25a3ab194093b7b082477caba5c554481f37bf1a"
assert module.CONFIRMATION == "FLASH-EXACT-U0O-RECOVERY"
assert module.EXPECTED_CANDIDATE_SHA256 == (
    "d98bb291f56fc8cb2f595c915d146c3b951333f04435dfb4e2839b95ddc5da0b"
)
assert module.EXPECTED_CANDIDATE_SIZE == 100663296
assert module.EXPECTED_MANIFEST_SHA256 == (
    "486387c863f55c28dec19128eff2a46d377d86762ae543aa2f1978292845b728"
)
assert module.EXPECTED_PATCH_SHA256 == (
    "f68c4dc7e605f8659553e7645db4f7e3cdfe47426bbf27906f740895671aea3a"
)
assert module.TRACE_PATH == "/var/log/a33x-u0o-real-boot-sshd.log"
assert module.base.WRITE_SCRIPT.count('dd if="$source" of="$target"') == 1
assert module.base.WRITE_SCRIPT.count("recovery_written=yes") == 1
assert "userdata_written=no" in module.base.WRITE_SCRIPT
assert "phone_reboot_performed=no" in module.base.WRITE_SCRIPT

script = module.TRACE_ABSENCE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "u0o_trace_baseline=unexpected-present",
    "u0o_trace_baseline=absent",
    "u0o_trace_baseline_unmount=passed",
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
):
    assert forbidden not in script, forbidden

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base.restore.local_evidence(root, repo)",
    "u0n_flash_v2.validate_phone_rootfs(adb, serial, local)",
    "u0o_trace_file_baseline_absent=passed",
    "base.rescue.verify_assets",
    "base.recovery_helper.prepare",
    "base.WRITE_SCRIPT",
    "recovery_previous_sha256",
    "persistent_trace_baseline",
    "flash_status",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "twrp reboot",
    "fastboot",
    "odin4 -a",
    "mount -o remount,rw",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "trace-absence.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_u0o_guarded_flash_self_test=passed")
print("exact_candidate_manifest_patch_and_audit_contract=passed")
print("shell_safe_rootfs_and_host_key_validation_reused=passed")
print("persistent_trace_absent_baseline_contract=passed")
print("exact_twrp_rescue_required_before_write=passed")
print("recovery_only_write_and_readback_contract=passed")
print("phone_reboot_and_other_partition_write_absence=passed")
print("shell_syntax_validation=passed")

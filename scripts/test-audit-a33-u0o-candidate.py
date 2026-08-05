#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0o-candidate.py"

spec = importlib.util.spec_from_file_location("a33_u0o_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BUILDER_BLOB == "56bee8bbf637fea7d0a077e1be2aed460dc85b7e"
assert module.EXPECTED_U0N_AUDIT_BLOB == "3152f2bbd504f842acd809156177b3c45cb7f800"
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")
assert module.builder.TRACE_PATH == "/var/log/a33x-u0o-real-boot-sshd.log"

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    before: dict[str, Path] = {}
    after: dict[str, Path] = {}
    for name in (*module.COMPONENTS_UNCHANGED, "ramdisk"):
        before[name] = root / f"before-{name}"
        after[name] = root / f"after-{name}"
        payload = name.encode() * 3
        before[name].write_bytes(payload)
        after[name].write_bytes(payload if name != "ramdisk" else payload + b"-u0o")
    result = module.compare_components(before, after)
    assert result["kernel_sha256"] == module.v2.sha_file(before["kernel"])
    assert result["u0n_ramdisk_sha256"] != result["u0o_ramdisk_sha256"]

source = MODULE.read_text(encoding="utf-8")
for required in (
    "builder.validate_parent(root, repo)",
    "builder.assert_only_init_changed(before, after)",
    "before.one(builder.WATCHDOG_TARGET).data != after.one(builder.WATCHDOG_TARGET).data",
    "builder.patch_init_second(original_init)",
    "u0n_watchdog_hook_byte_identical",
    "u0n_openrc_behavior_preserved",
    "persistent_trace_transformation_recomputed",
    "persistent_trace_file_count",
    "persistent_trace_scope_verified",
    "kernel_unchanged",
    "dtb_unchanged",
    "recovery_dtbo_unchanged",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "dd if=",
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "mkfs",
    "wipefs",
    "odin4",
    "fastboot",
):
    assert forbidden not in source, forbidden

print("a33_u0o_audit_self_test=passed")
print("u0n_parent_and_builder_blob_pins=passed")
print("init_2nd_only_payload_delta_contract=passed")
print("u0n_watchdog_hook_byte_identity_contract=passed")
print("one_file_persistent_trace_recomputation_contract=passed")
print("kernel_dtb_recovery_dtbo_identity_contract=passed")
print("host_only_and_phone_write_absence=passed")

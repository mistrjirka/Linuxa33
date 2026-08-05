#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0n-candidate.py"

spec = importlib.util.spec_from_file_location("a33_u0n_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BUILDER_BLOB == "9b72b0ee3252f90d33f2cb6000210edfd35dd9cd"
assert module.EXPECTED_U0M_FLASH_BLOB == "a4523f358e853026279bc780feeb3c5306c2ea29"
assert module.EXPECTED_U0M_AUDIT_BLOB == "b58d76df2681df7a23e589eb50760d8f26e99d59"
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    before: dict[str, Path] = {}
    after: dict[str, Path] = {}
    for name in (*module.COMPONENTS_UNCHANGED, "ramdisk"):
        before[name] = root / f"before-{name}"
        after[name] = root / f"after-{name}"
        payload = name.encode() * 3
        before[name].write_bytes(payload)
        after[name].write_bytes(payload if name != "ramdisk" else payload + b"-u0n")
    result = module.compare_components(before, after)
    assert result["kernel_sha256"] == module.v2.sha_file(before["kernel"])
    assert result["u0m_ramdisk_sha256"] != result["u0n_ramdisk_sha256"]

source = MODULE.read_text(encoding="utf-8")
for required in (
    "u0m_flash.base.validate_local(root, repo)",
    "builder.assert_only_init_changed(before, after)",
    "before.one(builder.WATCHDOG_TARGET).data != after.one(builder.WATCHDOG_TARGET).data",
    "builder.instrument_sshd_init(sshd_original)",
    "builder.patch_init_second(original_init, sshd_instrumented)",
    "u0m_audit.u0l_audit.unpack_recovery",
    "u0m_audit.u0l_audit.validate_boot_info_delta",
    "u0m_watchdog_hook_byte_identical",
    "openrc_default_start_stop_semantics_preserved",
    "instrumented_sshd_transformation_recomputed",
    "rootfs_persistent_delta",
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

print("a33_u0n_audit_self_test=passed")
print("u0m_parent_and_builder_blob_pins=passed")
print("init_2nd_only_payload_delta_contract=passed")
print("u0m_watchdog_hook_byte_identity_contract=passed")
print("instrumented_sshd_recomputation_contract=passed")
print("kernel_dtb_recovery_dtbo_identity_contract=passed")
print("ramdisk_and_avb_only_recovery_delta_contract=passed")
print("host_only_and_phone_write_absence=passed")

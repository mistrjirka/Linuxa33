#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0o-candidate-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0o_v2_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_AUDIT_BLOB == "2784ab9d46c39d49dc87802e09b30b30635e3407"
assert module.EXPECTED_BUILDER_V2_BLOB == "88cd0b9b3446314c04ad0c4b20583c2e6facf449"
assert module.base.builder.patch_init_second is module.builder_v2.patch_init_second
assert module.base.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    before: dict[str, Path] = {}
    after: dict[str, Path] = {}
    for name in (*module.base.COMPONENTS_UNCHANGED, "ramdisk"):
        before[name] = root / f"before-{name}"
        after[name] = root / f"after-{name}"
        payload = name.encode() * 3
        before[name].write_bytes(payload)
        after[name].write_bytes(payload if name != "ramdisk" else payload + b"-u0o-v2")
    result = module.base.compare_components(before, after)
    assert result["kernel_sha256"] == module.base.v2.sha_file(before["kernel"])
    assert result["u0n_ramdisk_sha256"] != result["u0o_ramdisk_sha256"]

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base.builder.patch_init_second = builder_v2.patch_init_second",
    "EXPECTED_BASE_AUDIT_BLOB",
    "EXPECTED_BUILDER_V2_BLOB",
    "return base.main()",
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

print("a33_u0o_v2_audit_self_test=passed")
print("base_audit_and_corrected_builder_blob_pins=passed")
print("corrected_persistent_trace_recomputation_wiring=passed")
print("kernel_dtb_recovery_dtbo_identity_contract=passed")
print("host_only_and_phone_write_absence=passed")

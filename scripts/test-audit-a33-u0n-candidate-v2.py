#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0n-candidate-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0n_v2_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_AUDIT_BLOB == "3152f2bbd504f842acd809156177b3c45cb7f800"
assert module.EXPECTED_BUILDER_V2_BLOB == "bbe8b22df2acc2dba3bbd79f30e1ef1165164799"
assert module.base.builder.instrument_sshd_init is module.builder_v2.instrument_sshd_init
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
        after[name].write_bytes(payload if name != "ramdisk" else payload + b"-u0n-v2")
    result = module.base.compare_components(before, after)
    assert result["kernel_sha256"] == module.base.v2.sha_file(before["kernel"])
    assert result["u0m_ramdisk_sha256"] != result["u0n_ramdisk_sha256"]

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base.builder.instrument_sshd_init = builder_v2.instrument_sshd_init",
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

print("a33_u0n_v2_audit_self_test=passed")
print("base_audit_and_corrected_builder_blob_pins=passed")
print("corrected_transformation_recomputation_wiring=passed")
print("kernel_dtb_recovery_dtbo_identity_contract=passed")
print("host_only_and_phone_write_absence=passed")

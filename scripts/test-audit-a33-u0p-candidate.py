#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0p-candidate.py"

spec = importlib.util.spec_from_file_location("a33_u0p_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BUILDER_BLOB == "2a5eb4957424fe81212e762ed2225f86ec890ca4"
assert module.EXPECTED_U0O_AUDIT_V2_BLOB == "25a3ab194093b7b082477caba5c554481f37bf1a"
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
        after[name].write_bytes(payload if name != "ramdisk" else payload + b"-u0p")
    result = module.compare_components(before, after)
    assert result["kernel_sha256"] == module.v2.sha_file(before["kernel"])
    assert result["u0o_ramdisk_sha256"] != result["u0p_ramdisk_sha256"]

source = MODULE.read_text(encoding="utf-8")
for required in (
    "before_embedded = builder.embedded_sshd_bytes(before_init)",
    "after_embedded = builder.embedded_sshd_bytes(after_init)",
    "before_embedded != after_embedded",
    "builder.declared_instrumented_sha(before_init)",
    "builder.declared_instrumented_sha(after_init)",
    "before_declared_hash_matches_embedded",
    "after_declared_hash_matches_embedded",
    "runtime_source_hash_contract",
    "instrumented-source-hash-mismatch",
    "u0o_watchdog_hook_byte_identical",
    "kernel_unchanged",
    "dtb_unchanged",
    "recovery_dtbo_unchanged",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "dd if=",
    "mkfs",
    "wipefs",
    "fastboot",
    "odin4",
    "mount -o remount,rw",
):
    assert forbidden not in source, forbidden

print("a33_u0p_audit_self_test=passed")
print("builder_and_u0o_audit_blob_pins=passed")
print("embedded_sshd_byte_identity_contract=passed")
print("stale_before_and_corrected_after_hash_contract=passed")
print("kernel_dtb_recovery_dtbo_identity_contract=passed")
print("host_only_and_phone_write_absence=passed")

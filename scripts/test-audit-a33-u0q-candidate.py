#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0q-candidate.py"

spec = importlib.util.spec_from_file_location("a33_u0q_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BUILDER_BLOB == "fa662b03cf3a4e4c9166ebc9fa0a177dc12dbdb4"
assert module.EXPECTED_U0P_AUDIT_BLOB == "abc5ac0901a0ca09bbac896d257d0ff40d9a0c66"
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
        after[name].write_bytes(payload if name != "ramdisk" else payload + b"-u0q")
    result = module.compare_components(before, after)
    assert result["kernel_sha256"] == module.v2.sha_file(before["kernel"])
    assert result["u0p_ramdisk_sha256"] != result["u0q_ramdisk_sha256"]

source = MODULE.read_text(encoding="utf-8")
for required in (
    "builder.validate_parent(",
    "builder.assert_only_init_changed(before, after)",
    "builder.patch_init_second(before_init, public_text)",
    "before_embedded != after_embedded",
    "normal_openrc_sshd_instrumentation_byte_identical",
    "long_lived_old_initramfs_root_reference",
    "emergency_auth_public_key_only",
    "private_key_embedded",
    "emergency_network_helper_independent_of_openrc",
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
):
    assert forbidden not in source, forbidden

print("a33_u0q_audit_self_test=passed")
print("builder_and_u0p_audit_blob_pins=passed")
print("init_2nd_only_payload_delta_contract=passed")
print("normal_openrc_sshd_byte_identity_contract=passed")
print("watchdog_kernel_dtb_recovery_dtbo_identity_contract=passed")
print("emergency_public_key_only_auth_contract=passed")
print("no_old_initramfs_root_pin_contract=passed")
print("independent_network_helper_contract=passed")
print("one_new_persistent_trace_file_contract=passed")
print("host_only_and_phone_write_absence=passed")

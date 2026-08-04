#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0m-candidate.py"
spec = importlib.util.spec_from_file_location("a33_u0m_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    before_root = root / "before"
    after_root = root / "after"
    before_root.mkdir()
    after_root.mkdir()
    before: dict[str, Path] = {}
    after: dict[str, Path] = {}
    for name in (*module.COMPONENTS_UNCHANGED, "ramdisk"):
        before[name] = before_root / name
        after[name] = after_root / name
        payload = f"same-{name}".encode()
        before[name].write_bytes(payload)
        after[name].write_bytes(payload)
    after["ramdisk"].write_bytes(b"changed-u0m-ramdisk")
    hashes = module.compare_component_sets(before, after)
    assert hashes["kernel_sha256"] == module.v2.sha_file(before["kernel"])
    assert hashes["u0l_ramdisk_sha256"] != hashes["u0m_ramdisk_sha256"]
    assert hashes["u0l_ramdisk_size"] == str(before["ramdisk"].stat().st_size)
    assert hashes["u0m_ramdisk_size"] == str(after["ramdisk"].stat().st_size)

    after["kernel"].write_bytes(b"unexpected-kernel-delta")
    try:
        module.compare_component_sets(before, after)
    except module.AuditError:
        pass
    else:
        raise AssertionError("unexpected U0m kernel delta was accepted")

    after["kernel"].write_bytes(before["kernel"].read_bytes())
    after["ramdisk"].write_bytes(before["ramdisk"].read_bytes())
    try:
        module.compare_component_sets(before, after)
    except module.AuditError:
        pass
    else:
        raise AssertionError("missing U0m ramdisk delta was accepted")

assert module.EXPECTED_U0M_BUILDER_BLOB == (
    "4ca8535ec430c171906b581f1e5f34073b852ba9"
)
assert module.EXPECTED_U0L_FLASH_BLOB == (
    "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
)
assert module.EXPECTED_U0L_AUDIT_BLOB == (
    "030c6313f133d5e1b7fef0be59ff1e54f65bc420"
)
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")

print("a33_u0m_candidate_audit_self_test=passed")
print("unchanged_kernel_dtb_recovery_dtbo_contract=passed")
print("changed_ramdisk_required=passed")
print("unexpected_component_delta_refusal=passed")
print("u0m_builder_u0l_flash_and_layout_audit_pinned=passed")

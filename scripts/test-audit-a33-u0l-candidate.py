#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0l-candidate.py"
spec = importlib.util.spec_from_file_location("a33_u0l_candidate_audit_test", MODULE)
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
    after["ramdisk"].write_bytes(b"changed-u0l-ramdisk")
    hashes = module.compare_component_sets(before, after)
    assert hashes["kernel_sha256"] == module.v2.sha_file(before["kernel"])
    assert hashes["u0k_ramdisk_sha256"] != hashes["u0l_ramdisk_sha256"]

    after["dtb"].write_bytes(b"unexpected-dtb-delta")
    try:
        module.compare_component_sets(before, after)
    except module.AuditError:
        pass
    else:
        raise AssertionError("unexpected DTB delta was accepted")

    after["dtb"].write_bytes(before["dtb"].read_bytes())
    after["ramdisk"].write_bytes(before["ramdisk"].read_bytes())
    try:
        module.compare_component_sets(before, after)
    except module.AuditError:
        pass
    else:
        raise AssertionError("missing ramdisk delta was accepted")

assert module.EXPECTED_U0L_BUILDER_BLOB == "c976721153b43e4507478597bb6680972b4cc8dc"
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")

print("a33_u0l_candidate_audit_self_test=passed")
print("unchanged_kernel_dtb_recovery_dtbo_contract=passed")
print("changed_ramdisk_required=passed")
print("unexpected_component_delta_refusal=passed")
print("u0l_builder_identity_pinned=passed")

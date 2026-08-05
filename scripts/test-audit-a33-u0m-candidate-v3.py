#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0m-candidate-v3.py"
spec = importlib.util.spec_from_file_location("a33_u0m_v3_audit_test", MODULE)
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
    after["ramdisk"].write_bytes(b"changed-u0m-v3-ramdisk")
    hashes = module.compare_components(before, after)
    assert hashes["u0l_ramdisk_sha256"] != hashes["u0m_ramdisk_sha256"]
    assert hashes["kernel_sha256"] == module.v2.sha_file(before["kernel"])

    after["dtb"].write_bytes(b"unexpected-dtb-delta")
    try:
        module.compare_components(before, after)
    except module.AuditError:
        pass
    else:
        raise AssertionError("unexpected U0m v3 DTB delta was accepted")

    after["dtb"].write_bytes(before["dtb"].read_bytes())
    after["ramdisk"].write_bytes(before["ramdisk"].read_bytes())
    try:
        module.compare_components(before, after)
    except module.AuditError:
        pass
    else:
        raise AssertionError("missing U0m v3 ramdisk delta was accepted")

assert module.EXPECTED_BUILDER_BLOB == "1e48bdd42905845046fc95e28e3cd597ae350df1"
assert module.EXPECTED_INSPECTOR_BLOB == "ea17562fba369bba3da81c291e22a15c663c929d"
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")
print("a33_u0m_v3_candidate_audit_self_test=passed")
print("unchanged_kernel_dtb_recovery_dtbo_contract=passed")
print("changed_ramdisk_required=passed")
print("unexpected_component_delta_refusal=passed")
print("builder_and_config_inspector_identity_pinned=passed")

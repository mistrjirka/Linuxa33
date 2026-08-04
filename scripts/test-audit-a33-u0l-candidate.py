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

u0k_info = """boot magic: ANDROID!
kernel size: 1234
ramdisk size: 456
command line args: console=ttySAC2 init=/sbin/init
header version: 2
"""
u0l_info = """boot magic: ANDROID!
kernel size: 1234
ramdisk size: 789
command line args: console=ttySAC2 init=/sbin/init
header version: 2
"""
assert module.normalize_boot_info(u0k_info) == module.normalize_boot_info(u0l_info)
changed_cmdline = u0l_info.replace("init=/sbin/init", "init=/bin/sh")
assert module.normalize_boot_info(u0k_info) != module.normalize_boot_info(changed_cmdline)
changed_kernel = u0l_info.replace("kernel size: 1234", "kernel size: 1235")
assert module.normalize_boot_info(u0k_info) != module.normalize_boot_info(changed_kernel)

assert module.EXPECTED_U0L_BUILDER_BLOB == "6c3133d5efbbdf08c3197eae3693d215fbf1b642"
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")
assert module.IGNORED_BOOT_INFO_PREFIXES == ("ramdisk size:", "ramdisk_size:")

print("a33_u0l_candidate_audit_self_test=passed")
print("unchanged_kernel_dtb_recovery_dtbo_contract=passed")
print("changed_ramdisk_required=passed")
print("unexpected_component_delta_refusal=passed")
print("ramdisk_size_only_normalization=passed")
print("command_line_change_refusal=passed")
print("kernel_header_change_refusal=passed")
print("u0l_builder_identity_pinned=passed")

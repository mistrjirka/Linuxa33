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
    assert hashes["u0k_ramdisk_size"] == str(before["ramdisk"].stat().st_size)
    assert hashes["u0l_ramdisk_size"] == str(after["ramdisk"].stat().st_size)

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


def boot_info(ramdisk_size: int, recovery_offset: int, *, cmdline: str = "init=/sbin/init") -> str:
    return f"""boot magic: ANDROID!
kernel size: 8192
kernel load address: 0x10008000
ramdisk size: {ramdisk_size}
ramdisk load address: 0x10000000
second bootloader size: 0
second bootloader load address: 0x00000000
kernel tags load address: 0x10000000
page size: 4096
boot image header version: 2
command line args: {cmdline}
additional command line args:
recovery dtbo size: 1024
recovery dtbo offset: 0x{recovery_offset:016x}
dtb size: 2048
dtb address: 0x0000000010000000
"""


u0k_ramdisk_size = 4097
u0l_ramdisk_size = 8193
u0k_offset = 0x5000
u0l_offset = 0x6000
u0k_info = boot_info(u0k_ramdisk_size, u0k_offset)
u0l_info = boot_info(u0l_ramdisk_size, u0l_offset)
layout = module.validate_boot_info_delta(
    u0k_info,
    u0l_info,
    before_ramdisk_size=u0k_ramdisk_size,
    after_ramdisk_size=u0l_ramdisk_size,
)
assert layout["u0k_recovery_dtbo_offset"] == "0x5000"
assert layout["u0l_recovery_dtbo_offset"] == "0x6000"
assert layout["recovery_dtbo_offset_formula_verified"] == "yes"
assert module.parse_boot_info("ramdisk_size: 7\n")["ramdisk size"] == "7"

for bad_after in (
    boot_info(u0l_ramdisk_size, u0l_offset, cmdline="init=/bin/sh"),
    boot_info(u0l_ramdisk_size, u0l_offset).replace("kernel size: 8192", "kernel size: 8193"),
    boot_info(u0l_ramdisk_size, 0x7000),
):
    try:
        module.validate_boot_info_delta(
            u0k_info,
            bad_after,
            before_ramdisk_size=u0k_ramdisk_size,
            after_ramdisk_size=u0l_ramdisk_size,
        )
    except module.AuditError:
        pass
    else:
        raise AssertionError("unsafe boot-info delta was accepted")

try:
    module.validate_boot_info_delta(
        u0k_info,
        u0l_info,
        before_ramdisk_size=u0k_ramdisk_size + 1,
        after_ramdisk_size=u0l_ramdisk_size,
    )
except module.AuditError:
    pass
else:
    raise AssertionError("header/extracted ramdisk-size mismatch was accepted")

assert module.EXPECTED_U0L_BUILDER_BLOB == "6c3133d5efbbdf08c3197eae3693d215fbf1b642"
assert module.COMPONENTS_UNCHANGED == ("kernel", "dtb", "recovery_dtbo")
assert module.BOOT_INFO_LAYOUT_FIELDS == ("ramdisk size", "recovery dtbo offset")

print("a33_u0l_candidate_audit_self_test=passed")
print("unchanged_kernel_dtb_recovery_dtbo_contract=passed")
print("changed_ramdisk_required=passed")
print("unexpected_component_delta_refusal=passed")
print("ramdisk_size_and_layout_offset_validation=passed")
print("command_line_change_refusal=passed")
print("kernel_header_change_refusal=passed")
print("invalid_recovery_dtbo_offset_refusal=passed")
print("header_component_size_crosscheck=passed")
print("u0l_builder_identity_pinned=passed")

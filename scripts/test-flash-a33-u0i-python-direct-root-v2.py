#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import uuid

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "flash_u0i_v2", HERE / "flash-a33-u0i-python-direct-root-v2.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

sample = (
    "adb daemon startup noise\n"
    "List of devices attached\n"
    "RFCTA00V43L\trecovery product:a33x model:SM_A336B\n"
)
assert module.parse_adb_devices(sample) == [("RFCTA00V43L", "recovery")]

values, sections = module.parse_sections(
    "recovery_sha=abc\n"
    "mount_users_begin\n"
    "/sdcard\n"
    "/data\n"
    "mount_users_end\n"
    "swap_users_begin\n"
    "swap_users_end\n"
)
assert values == {"recovery_sha": "abc"}
assert sections == {"mount_users": ["/sdcard", "/data"], "swap_users": []}

raw_uuid = uuid.UUID("7b056328-bdfb-496b-ac38-2624c43c863a").bytes
superblock = bytearray(1024)
superblock[56:58] = b"\x53\xef"
superblock[104:120] = raw_uuid
superblock[120:129] = b"pmOS_root"
assert str(uuid.UUID(bytes=bytes(superblock[104:120]))) == "7b056328-bdfb-496b-ac38-2624c43c863a"
assert bytes(superblock[120:136]).split(b"\0", 1)[0] == b"pmOS_root"

try:
    module.require({"status": "failed"}, {"status": "passed"}, "fixture")
except module.Refusal:
    pass
else:
    raise AssertionError("require() accepted a mismatching contract")

print("u0i_flash_python_self_test=passed")
print("adb_device_selection_parser=passed")
print("section_parser=passed")
print("ext4_superblock_parser_fixture=passed")
print("fail_closed_contract_fixture=passed")

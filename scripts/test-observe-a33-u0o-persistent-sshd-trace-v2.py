#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0o-persistent-sshd-trace-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0o_observer_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "952ce1d03b79f4cb4d29ad83600d2220be727e01"
assert module.base.adb_boot_id is module.adb_boot_id

original_run = module.base.common.run
try:
    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="adb", timeout=2)

    module.base.common.run = timeout_run
    assert module.adb_boot_id("adb", "serial") == ""

    class Result:
        returncode = 0
        stdout = "new-boot-id\r\n"

    module.base.common.run = lambda *args, **kwargs: Result()
    assert module.adb_boot_id("adb", "serial") == "new-boot-id"
finally:
    module.base.common.run = original_run

source = MODULE.read_text(encoding="utf-8")
for required in (
    "except subprocess.TimeoutExpired:",
    'return ""',
    "base.adb_boot_id = adb_boot_id",
    "return base.main()",
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

print("a33_u0o_observer_v2_self_test=passed")
print("exact_base_observer_blob_pin=passed")
print("adb_timeout_classified_as_old_transport_unavailable=passed")
print("dual_adb_and_usb_transition_requirement_preserved=passed")
print("phone_write_scope_unchanged=passed")

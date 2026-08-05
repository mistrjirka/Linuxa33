#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "observe-a33-u0n-real-boot-sshd-trace-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0n_observer_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_OBSERVER_BLOB == "31b6f288ddeb743afb8b338b08c7169dbfe4f31e"
assert module.EXPECTED_FLASH_V2_BLOB == "337807470888e0d00a6afb40a5a7ce7bcd8875c3"
assert module.base.flash is module.flash_v2.base
assert module.base.common is module.flash_v2.base.common
assert module.base.flash.validate_phone_rootfs is module.flash_v2.validate_phone_rootfs
assert module.base.OBSERVATION_SECONDS == 90

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base.flash = flash_v2.base",
    "base.common = flash_v2.base.common",
    "EXPECTED_BASE_OBSERVER_BLOB",
    "EXPECTED_FLASH_V2_BLOB",
    "return base.main()",
):
    assert required in source, required
for forbidden in (
    "adb reboot recovery",
    "dd if=",
    "mkfs",
    "wipefs",
    "fastboot",
):
    assert forbidden not in source, forbidden

print("a33_u0n_observer_v2_self_test=passed")
print("base_observer_and_flash_v2_blob_pins=passed")
print("shell_safe_phone_preflight_routing=passed")
print("full_90_second_observer_contract_preserved=passed")
print("phone_write_scope_unchanged=passed")

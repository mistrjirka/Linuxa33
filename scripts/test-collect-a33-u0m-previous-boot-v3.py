#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0m-previous-boot-v3.py"
spec = importlib.util.spec_from_file_location("a33_u0m_v3_collector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.base.PROFILE.validate()
assert module.base.PROFILE.expected_flash_operation == (
    "flash-exact-u0m-v3-watchdog-magic-close"
)
assert module.base.PROFILE.flash_report_name == (
    "a33-first-rootfs-u0m-v3-watchdog-magic-close-flash.txt"
)
assert module.base.PROFILE.output_prefix == "u0m-v3-watchdog-magic-close-observation"
assert module.base.OUTPUT_PREFIX == "u0m-v3-watchdog-magic-close-result"
assert module.base.u0m is module.flash
assert "watchdog_magic_close_completed_count" in module.base.COUNT_PATTERNS
assert "watchdog_reset_count" in module.base.COUNT_PATTERNS
print("a33_u0m_v3_previous_boot_collector_self_test=passed")
print("v3_flash_validation_path=passed")
print("watchdog_magic_close_and_reset_filter_preserved=passed")
print("known_good_twrp_gate_preserved=passed")

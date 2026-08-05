#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "boot-observe-a33-u0m-watchdog-magic-close-v2.py"
spec = importlib.util.spec_from_file_location("a33_u0m_observer_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.PROFILE.validate()
assert module.PROFILE.expected_flash_operation == (
    "flash-exact-u0m-watchdog-magic-close"
)
assert module.PROFILE.flash_report_name == (
    "a33-first-rootfs-u0m-watchdog-magic-close-flash.txt"
)
assert module.PROFILE.output_prefix == "u0m-watchdog-magic-close-observation"
assert module.flash_v2.base.validate_local
assert module.flash_v2.EXPECTED_U0M_BUILDER_BLOB == (
    "19cb63ea55ecfb7a186016058b7303b4326c9030"
)

print("u0m_observer_v2_profile_self_test=passed")
print("v2_flash_validation_path=passed")
print("shared_observer_contract=passed")

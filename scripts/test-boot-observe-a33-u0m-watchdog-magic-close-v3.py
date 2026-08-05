#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "boot-observe-a33-u0m-watchdog-magic-close-v3.py"
spec = importlib.util.spec_from_file_location("a33_u0m_v3_observer_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.PROFILE.validate()
assert module.PROFILE.expected_flash_operation == (
    "flash-exact-u0m-v3-watchdog-magic-close"
)
assert module.PROFILE.flash_report_name == (
    "a33-first-rootfs-u0m-v3-watchdog-magic-close-flash.txt"
)
assert module.PROFILE.output_prefix == "u0m-v3-watchdog-magic-close-observation"
assert module.PROFILE.observation_operation == "observe-u0m-v3-watchdog-magic-close"
assert module.flash.PROFILE.operation == module.PROFILE.expected_flash_operation
print("u0m_v3_observer_profile_self_test=passed")
print("v3_flash_validation_path=passed")
print("shared_network_and_ssh_observer_contract=passed")

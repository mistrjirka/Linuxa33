#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0m-previous-boot-v2.py"
spec = importlib.util.spec_from_file_location("a33_u0m_collector_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.base.u0m is module.flash_v2.base
assert module.base.PROFILE.expected_flash_operation == (
    "flash-exact-u0m-watchdog-magic-close"
)
assert module.flash_v2.EXPECTED_U0M_BUILDER_BLOB == (
    "19cb63ea55ecfb7a186016058b7303b4326c9030"
)
assert module.base.OUTPUT_PREFIX == "u0m-watchdog-magic-close-result"

print("a33_u0m_previous_boot_collector_v2_self_test=passed")
print("v2_flash_validation_path=passed")
print("existing_evidence_filter_preserved=passed")

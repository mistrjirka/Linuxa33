#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0m-watchdog-magic-close-v2.py"
spec = importlib.util.spec_from_file_location("a33_u0m_flash_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0M_BUILDER_BLOB == (
    "19cb63ea55ecfb7a186016058b7303b4326c9030"
)
assert module.EXPECTED_U0M_AUDIT_V2_BLOB == (
    "80cee6825ea96ef18799ab46828d9f3fb0b566cd"
)
assert module.base.U0M_AUDIT == module.AUDIT_V2
assert module.base.EXPECTED_U0M_BUILDER_BLOB == module.EXPECTED_U0M_BUILDER_BLOB
assert module.base.EXPECTED_U0M_AUDIT_BLOB == module.EXPECTED_U0M_AUDIT_V2_BLOB
assert module.base.PROFILE.operation == "flash-exact-u0m-watchdog-magic-close"
assert module.base.PROFILE.report_name == (
    "a33-first-rootfs-u0m-watchdog-magic-close-flash.txt"
)

print("u0m_flash_v2_entrypoint_self_test=passed")
print("updated_builder_and_audit_pins=passed")
print("recovery_only_profile_preserved=passed")

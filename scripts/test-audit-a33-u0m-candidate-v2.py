#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0m-candidate-v2.py"
spec = importlib.util.spec_from_file_location("a33_u0m_audit_v2_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0M_BUILDER_BLOB == (
    "19cb63ea55ecfb7a186016058b7303b4326c9030"
)
assert module.base.EXPECTED_U0M_BUILDER_BLOB == module.EXPECTED_U0M_BUILDER_BLOB
assert module.base.u0m.NOWAYOUT_PARAMETER == (
    "/sys/module/s3c2410_wdt/parameters/nowayout"
)
assert module.base.u0m.STOP_LOG == "Watchdog cluster 0 stop done"
assert module.base.u0m.DID_NOT_STOP_LOG == "watchdog0: watchdog did not stop!"

print("a33_u0m_candidate_audit_v2_self_test=passed")
print("updated_builder_identity_pinned=passed")
print("driver_log_watchdog_contract_visible=passed")

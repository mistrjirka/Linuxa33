#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))

spec = importlib.util.spec_from_file_location(
    "u0m_observer", HERE / "boot-observe-a33-u0m-watchdog-magic-close.py"
)
assert spec and spec.loader
observer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = observer
spec.loader.exec_module(observer)

observer.PROFILE.validate()
assert observer.PROFILE.expected_flash_operation == "flash-exact-u0m-watchdog-magic-close"
assert observer.PROFILE.flash_report_name == "a33-first-rootfs-u0m-watchdog-magic-close-flash.txt"
assert observer.PROFILE.output_prefix == "u0m-watchdog-magic-close-observation"
assert observer.PROFILE.observation_operation == "observe-u0m-watchdog-magic-close"

try:
    observer.ObserverProfile(
        expected_flash_operation="bad operation",
        flash_report_name="../bad.txt",
        output_prefix="bad/path",
        observation_operation="bad operation",
    ).validate()
except ValueError:
    pass
else:
    raise AssertionError("unsafe U0m observer profile was accepted")

print("u0m_observer_profile_self_test=passed")
print("u0m_flash_report_contract=passed")
print("unsafe_observer_profile_refusal=passed")
print("shared_observer_contract=passed")

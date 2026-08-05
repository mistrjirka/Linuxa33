#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0m_v4_builder_test", HERE / "make-u0m-watchdog-magic-close-v4.py")
audit = load("a33_u0m_v4_audit_test", HERE / "audit-a33-u0m-candidate-v4.py")
flash = load("a33_u0m_v4_flash_test", HERE / "flash-a33-u0m-watchdog-magic-close-v4.py")
observer = load(
    "a33_u0m_v4_observer_test",
    HERE / "boot-observe-a33-u0m-watchdog-magic-close-v4.py",
)
collector = load(
    "a33_u0m_v4_collector_test",
    HERE / "collect-a33-u0m-previous-boot-v4.py",
)

assert builder.base.base.u0l.u0j is builder.base.base.u0l.u0k.u0j
assert audit.base.builder.base.u0l.u0j is audit.base.builder.base.u0l.u0k.u0j
assert flash.base.builder.base.u0l.u0j is flash.base.builder.base.u0l.u0k.u0j
assert flash.base.AUDIT == flash.AUDIT
assert observer.flash.base.PROFILE.operation == (
    "flash-exact-u0m-v3-watchdog-magic-close"
)
assert observer.PROFILE.expected_flash_operation == (
    "flash-exact-u0m-v3-watchdog-magic-close"
)
assert collector.base.u0m is collector.flash.base
assert collector.base.PROFILE.flash_report_name == (
    "a33-first-rootfs-u0m-v3-watchdog-magic-close-flash.txt"
)
assert collector.base.OUTPUT_PREFIX == "u0m-v4-watchdog-magic-close-result"

print("a33_u0m_v4_chain_self_test=passed")
print("u0j_import_path_fixed=passed")
print("host_config_pinned_builder_preserved=passed")
print("v4_audit_and_flash_path=passed")
print("v4_observer_and_collector_path=passed")
print("recovery_only_profile_preserved=passed")

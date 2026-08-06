#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PATHS = {
    "builder": HERE / "make-u0q-emergency-ssh-v3.py",
    "audit": HERE / "audit-a33-u0q-candidate-v3.py",
    "flash": HERE / "flash-a33-u0q-emergency-ssh-v3.py",
    "observer": HERE / "observe-a33-u0q-emergency-ssh-v3.py",
    "collector": HERE / "collect-a33-u0q-emergency-ssh-v3.py",
}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


builder = load("a33_u0q_v3_chain_builder", PATHS["builder"])
audit = load("a33_u0q_v3_chain_audit", PATHS["audit"])
flash = load("a33_u0q_v3_chain_flash", PATHS["flash"])
observer = load("a33_u0q_v3_chain_observer", PATHS["observer"])
collector = load("a33_u0q_v3_chain_collector", PATHS["collector"])

assert builder.RUNTIME_REVISION == "3"
assert builder.MOUNT_POLICY == "verify-or-create-proc-sys-dev-devpts-run"
assert audit.EXPECTED_BUILDER_V3_BLOB == "295f1979a5a411dfec5456b5929f50d4286b0e6f"
assert flash.EXPECTED_BUILDER_V3_BLOB == audit.EXPECTED_BUILDER_V3_BLOB
assert flash.EXPECTED_AUDIT_V3_BLOB == "4fd86baa144355e7d8aae75a8bd5975873916eda"
assert flash.CONFIRMATION == "FLASH-EXACT-U0Q-V3-RECOVERY"
assert observer.EXPECTED_FLASH_V3_BLOB == "79e8b0dd2a2a781018b027b551f54796e4608afb"
assert collector.EXPECTED_FLASH_V3_BLOB == observer.EXPECTED_FLASH_V3_BLOB
assert collector.EXPECTED_OBSERVER_V3_BLOB == (
    "37e4e8a747e6ae45f332304ae8fff1079f794cda"
)

for label, path in PATHS.items():
    source = path.read_text(encoding="utf-8")
    assert "phone_partition_writes" in source, label
    assert "fastboot" not in source, label
    assert "odin4 -a" not in source, label

assert "runtime_mount_policy" in PATHS["flash"].read_text(encoding="utf-8")
assert "u0q-v3-emergency-ssh-observation" in PATHS["observer"].read_text(
    encoding="utf-8"
)
assert "u0q-v3-emergency-ssh-result" in PATHS["collector"].read_text(
    encoding="utf-8"
)

print("a33_u0q_v3_runtime_chain_self_test=passed")
print("v3_builder_to_audit_blob_pin=passed")
print("v3_audit_to_flash_blob_pin=passed")
print("v3_flash_to_observer_blob_pin=passed")
print("v3_observer_to_collector_blob_pin=passed")
print("v2_phone_entrypoints_not_routed_by_v3_chain=passed")
print("recovery_only_write_scope_preserved=passed")
print("host_observer_and_read_only_collector_scope_preserved=passed")

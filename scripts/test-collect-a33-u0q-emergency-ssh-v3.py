#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0q-emergency-ssh-v3.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v3_collector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_FLASH_V3_BLOB == (
    "79e8b0dd2a2a781018b027b551f54796e4608afb"
)
assert module.EXPECTED_OBSERVER_V3_BLOB == (
    "37e4e8a747e6ae45f332304ae8fff1079f794cda"
)
assert module.EXPECTED_COLLECTOR_V2_BLOB == (
    "23b5f665876b7053bc6cadc488a4528e1640a542"
)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "u0q-v3-emergency-ssh-observation-*",
    "audit_v3_sha256",
    "reboot_transition_verified",
    "passed-transition-proven-u0q-v3-emergency-ssh-authenticated",
    "common.KNOWN_TWRP_SHA256",
    "validate_phone_rootfs",
    "v2_collector.TRACE_READ_SCRIPT",
    "v2_collector.decode_trace",
    "event=runtime-mounts-ready",
    "u0q-v3-runtime-mount-preparation-did-not-complete",
    "u0q-v3-live-emergency-ssh-and-diagnostics-succeeded",
    "last_kmsg.bin",
    "host-evidence",
    "phone_partition_writes",
    "phone_reboot_performed",
    "userdata_persistent_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4 -a",
    "base.WRITE_SCRIPT",
    "mount -o remount,rw",
    "umount -l",
):
    assert forbidden not in source, forbidden

assert "mount -t ext4 -o ro,noload,nosuid,nodev,noatime" in (
    module.v2_collector.TRACE_READ_SCRIPT
)
assert "emit_trace emergency" in module.v2_collector.TRACE_READ_SCRIPT
assert "emit_trace inherited" in module.v2_collector.TRACE_READ_SCRIPT
assert module.v2_collector.MAX_TRACE_BYTES == 4 * 1024 * 1024

print("a33_u0q_v3_collector_self_test=passed")
print("exact_v3_flash_observer_and_v2_collector_blob_pins=passed")
print("transition_proven_v3_observation_requirement=passed")
print("exact_twrp_restore_verification_contract=passed")
print("dual_trace_read_only_noload_transport_reused=passed")
print("runtime_mount_failure_classification_contract=passed")
print("emergency_and_inherited_trace_evidence_archive_contract=passed")
print("phone_write_and_reboot_absence=passed")

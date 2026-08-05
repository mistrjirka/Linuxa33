#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "analyze-a33-u0n-result.py"

spec = importlib.util.spec_from_file_location("a33_u0n_result_analysis_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    archive_path = root / "result.tar.gz"
    prefix = "u0n-result"
    summary = {"candidate_sha256": module.EXPECTED_CANDIDATE_SHA256}
    observation_summary = {
        "candidate_sha256": module.EXPECTED_CANDIDATE_SHA256,
        "observation_seconds": 90,
        "observation_status": "passed-full-90-second-window",
    }
    rows = [
        {
            "usb_enumeration": True,
            "usb_line": "Bus 003 Device 022: ID 04e8:6860 Samsung",
            "host_usb_network_interface": False,
            "ping_172_16_42_1": False,
            "ssh_banner": False,
            "tcp22_state": "connect-error-11",
        }
        for _ in range(3)
    ]
    last_kmsg = (
        "[18418.889] Kernel panic - not syncing: Hard Reset Hook\n"
        "[18418.907] 0 477 1 recovery\n"
        "5.10.66-Gabriel260BR-TWRP-ga0103aac9499\n"
    )
    members = {
        f"{prefix}/summary.json": json.dumps(summary).encode(),
        f"{prefix}/observation/summary.json": json.dumps(observation_summary).encode(),
        f"{prefix}/observation/observation.jsonl": (
            "\n".join(json.dumps(row) for row in rows) + "\n"
        ).encode(),
        f"{prefix}/last_kmsg.sanitized.txt": last_kmsg.encode(),
    }
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))

    result = module.analyze_archive(archive_path)
    assert result["diagnosis"] == "last-kmsg-preserved-pre-u0n-twrp-hard-reset-not-u0n"
    assert result["u0n_execution_status"] == "unproven"
    assert result["persistent_trace_required"] is True
    assert result["observer_must_verify_old_adb_disappears"] is True
    assert result["usb_identity_unique_count"] == 1
    assert result["reboot_transition_evidence"] == "single-unchanged-usb-identity"
    assert result["last_kmsg_u0n_markers"] == 0
    assert result["last_kmsg_max_recovery_uptime_seconds"] == 18418.907
    assert result["tcp22_state_counts"] == {"connect-error-11": 3}
    assert result["phone_partition_writes"] == "no"

source = MODULE.read_text(encoding="utf-8")
for forbidden in (
    "adb reboot",
    "dd if=",
    "mkfs",
    "wipefs",
    "fastboot",
    "odin4",
):
    assert forbidden not in source, forbidden

print("a33_u0n_result_analysis_self_test=passed")
print("long_running_twrp_last_kmsg_classification=passed")
print("unchanged_usb_identity_and_reboot_unverified_contract=passed")
print("persistent_trace_requirement_contract=passed")
print("host_only_and_phone_write_absence=passed")

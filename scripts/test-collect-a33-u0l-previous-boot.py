#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "collect-a33-u0l-previous-boot.py"
spec = importlib.util.spec_from_file_location("a33_u0l_collector_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

raw = b"hello\x00a33x-u0l-openrc-cgroup-isolation: stage=mask-begin\n\xfftail\n"
san = module.sanitize_last_kmsg(raw)
assert "hello\na33x-u0l" in san
assert "\ufffdtail" in san

fixture = """
a33x-u0k-direct-mount: stage=mount-root-success
a33x-u0k-direct-mount: stage=cleanup-hooks-done
a33x-u0l-openrc-cgroup-isolation: stage=mask-begin
a33x-u0l-openrc-cgroup-isolation: stage=mask-success
a33x-u0k-direct-mount: stage=switch-root-begin
OpenRC 0.63.2
sshd starting
"""
counts = module.count_summary(fixture)
assert counts["u0l_mask_begin_count"] == 1
assert counts["u0l_mask_success_count"] == 1
assert counts["u0l_mask_error_count"] == 0
assert counts["u0k_mount_success_count"] == 1
assert counts["u0k_cleanup_done_count"] == 1
assert counts["u0k_switch_root_begin_count"] == 1
assert counts["openrc_count"] == 1
assert counts["sshd_count"] == 1
focused = module.focused_lines(fixture)
assert any("mask-success" in line for line in focused)
assert any("OpenRC" in line for line in focused)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    destination = root / "capture.txt"

    class DummyCommon:
        @staticmethod
        def run(args, *, text, check, timeout):
            assert check is False
            return subprocess.CompletedProcess(
                args=args,
                returncode=0,
                stdout="captured\n" if text else b"captured\n",
                stderr="" if text else b"",
            )

    payload = module.capture_command(
        DummyCommon,
        ["fixture"],
        destination,
        required=True,
    )
    assert payload == b"captured\n"
    assert destination.read_text(encoding="utf-8") == "captured\n"

    result_root = root / "results"
    result_root.mkdir()
    first = result_root / f"{module.PROFILE.output_prefix}-1"
    second = result_root / f"{module.PROFILE.output_prefix}-2"
    first.mkdir()
    second.mkdir()
    first.touch()
    second.touch()
    assert module.latest_observation(result_root) in {first, second}

module.PROFILE.validate()
assert module.OUTPUT_PREFIX == "u0l-openrc-cgroup-isolation-result"
print("a33_u0l_previous_boot_collector_self_test=passed")
print("binary_last_kmsg_sanitizer=passed")
print("u0l_marker_summary=passed")
print("focused_log_filter=passed")
print("capture_contract_fixture=passed")
print("latest_observation_selection=passed")
print("known_good_twrp_gate_present=passed")

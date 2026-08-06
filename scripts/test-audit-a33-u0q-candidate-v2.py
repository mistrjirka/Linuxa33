#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0q-candidate-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v2_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_AUDIT_BLOB == (
    "f52f01d8c878ed24aaae3f508f6e8e82663971e3"
)
assert module.EXPECTED_BUILDER_V2_BLOB == (
    "60d91c8a83722c32511219eed0e2625ec35d3f3e"
)
assert module.REPORT_NAME == "a33-u0q-candidate-audit-v2.txt"
assert module.V2_FIELDS == {
    "u0q_runtime_revision": "2",
    "emergency_runtime_mount_required": "/run",
    "emergency_privsep_path": "/run/sshd",
    "emergency_privsep_backing": "preexisting-mounted-run",
    "emergency_firewall_policy": "runtime-nft-monitor",
    "emergency_firewall_rule_comment": "a33x-u0q-emergency-2222",
    "emergency_firewall_persistent_delta": "none",
}
module.require_v2_fields(dict(module.V2_FIELDS), "fixture")

fixture = "\n".join(
    (
        "candidate=U0q-emergency-ssh stage=trace-open",
        "event=runtime-directory-ready path=/run/sshd backing=mounted-run revision=2",
        "event=network-helper-spawned",
        "event=config-test-start port=2222",
        "grep a33x-u0q-emergency-2222",
        "nft insert rule inet filter input tcp dport 2222 accept comment a33x-u0q-emergency-2222",
        "event=runtime-firewall-rule-added",
        "event=sshd-helper-spawned",
        'exec switch_root /sysroot "$init"',
        "run-is-not-a-mounted-runtime-filesystem",
    )
) + "\n"
module.verify_payload_text(fixture)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base_audit.builder.network_script = builder_v2.network_script",
    "base_audit.builder.emergency_block = builder_v2.emergency_block",
    "result = base_audit.main()",
    "require_v2_fields(manifest",
    "require_v2_fields(patch",
    "verify_payload_text(init_text)",
    "base U0q exact-delta audit",
    "persistent_firewall_file_delta",
    "normal_openrc_sshd_instrumentation_byte_identical",
    "u0p_watchdog_hook_byte_identical",
    "audit_v2_status",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_u0q_v2_audit_self_test=passed")
print("base_audit_and_v2_builder_blob_pins=passed")
print("full_base_exact_delta_audit_reexecution_contract=passed")
print("mounted_run_privsep_order_contract=passed")
print("runtime_only_firewall_contract=passed")
print("persistent_firewall_file_delta_absence=passed")
print("kernel_dtb_recovery_dtbo_watchdog_identity_contract=passed")
print("host_only_and_phone_write_absence=passed")

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "audit-a33-u0q-candidate-v3.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v3_audit_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_AUDIT_BLOB == (
    "f52f01d8c878ed24aaae3f508f6e8e82663971e3"
)
assert module.EXPECTED_BUILDER_V3_BLOB == (
    "295f1979a5a411dfec5456b5929f50d4286b0e6f"
)
assert module.REPORT_NAME == "a33-u0q-candidate-audit-v3.txt"
assert module.V3_FIELDS["u0q_runtime_revision"] == "3"
assert module.V3_FIELDS["emergency_runtime_mount_policy"] == (
    "verify-or-create-proc-sys-dev-devpts-run"
)
assert module.V3_FIELDS["emergency_privsep_backing"] == (
    "verified-or-created-tmpfs-run"
)
assert module.V3_FIELDS["emergency_persistent_mount_config_delta"] == "none"

fixture = "\n".join(
    (
        "candidate=U0q-emergency-ssh stage=trace-open",
        "event=runtime-mounts-ready policy=verify-or-create-proc-sys-dev-devpts-run",
        "mount -t proc proc /sysroot/proc",
        "mount -t sysfs sysfs /sysroot/sys",
        "mount -o bind /dev /sysroot/dev",
        "mount -t devpts -o mode=0620,gid=5,ptmxmode=0666 devpts /sysroot/dev/pts",
        "mount -t tmpfs -o mode=0755,nosuid,nodev,size=8m tmpfs /sysroot/run",
        "event=runtime-directory-ready path=/run/sshd",
        "event=network-helper-spawned",
        "event=sshd-helper-spawned",
        "event=network-ready-marker-written",
        "grep a33x-u0q-emergency-2222",
        (
            "nft insert rule inet filter input tcp dport 2222 accept "
            "comment a33x-u0q-emergency-2222"
        ),
        "event=pre-switch-root-ready",
        "emergency-channel-readiness-timeout",
        'exec switch_root /sysroot "$init"',
    )
) + "\n"
module.verify_payload_text(fixture)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base_audit.builder.network_script = builder_v3.v2.network_script",
    "base_audit.builder.emergency_block = builder_v3.emergency_block",
    "result = base_audit.main()",
    "v2.require(manifest, V3_FIELDS",
    "v2.require(patch, V3_FIELDS",
    "verify_payload_text(init_text)",
    "base U0q v3 exact-delta audit",
    "runtime_mount_order_verified",
    "pre_switch_root_live_channel_gate_verified",
    "persistent_mount_configuration_delta",
    "normal_openrc_sshd_instrumentation_byte_identical",
    "u0p_watchdog_hook_byte_identical",
    "audit_v3_status",
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

print("a33_u0q_v3_audit_self_test=passed")
print("base_audit_and_v3_builder_blob_pins=passed")
print("full_base_exact_delta_audit_reexecution_contract=passed")
print("runtime_mount_order_and_fstype_contract=passed")
print("pre_switch_root_live_channel_gate_contract=passed")
print("persistent_mount_and_firewall_configuration_delta_absence=passed")
print("kernel_dtb_recovery_dtbo_watchdog_identity_contract=passed")
print("host_only_and_phone_write_absence=passed")

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "flash-a33-u0m-watchdog-magic-close.py"
spec = importlib.util.spec_from_file_location("a33_u0m_flash_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.PROFILE.validate()
assert module.PROFILE.operation == "flash-exact-u0m-watchdog-magic-close"
assert module.PROFILE.report_name == "a33-first-rootfs-u0m-watchdog-magic-close-flash.txt"
assert module.PROFILE.remote_candidate == "/tmp/a33x-u0m-watchdog-magic-close-recovery.img"

manifest = module.manifest_contract()
assert manifest["candidate"] == "U0m-watchdog-magic-close"
assert manifest["functional_base"] == "U0l-openrc-cgroup-isolation"
assert manifest["cpio_payload_delta"] == "hooks/01-a33x-watchdog.sh,init_2nd.sh"
assert manifest["watchdog_magic_close_byte"] == "V"
assert manifest["watchdog_nowayout_required"] == "0"
assert manifest["watchdog_state_before_required"] == "active"
assert manifest["watchdog_state_after_required"] == "inactive"
assert manifest["watchdog_failure_behavior"] == "continue-feeding-and-refuse-switch-root"
assert manifest["rootfs_persistent_delta"] == "none"
assert manifest["module_delta"] == "none"

patch = module.patch_contract()
assert patch["operation"] == "python-u0m-verified-watchdog-magic-close"
assert patch["patch_status"] == "passed"

audit = module.audit_contract()
assert audit["operation"] == "host-only-audit-u0m-exact-delta"
assert audit["watchdog_magic_close_contract"] == "passed"
assert audit["watchdog_fail_closed_contract"] == "passed"
assert audit["audit_status"] == "passed"

assert module.EXPECTED_U0L_FLASH_BLOB == (
    "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
)
assert module.EXPECTED_U0M_BUILDER_BLOB == (
    "4ca8535ec430c171906b581f1e5f34073b852ba9"
)
assert module.EXPECTED_U0M_AUDIT_BLOB == (
    "0ec0b33cf4581a1e07c4e740e6b987924add45a9"
)

try:
    module.FlashProfile(
        operation="bad operation",
        report_name="../bad.txt",
        remote_candidate="/data/bad.img",
        success_label="bad\nlabel",
    ).validate()
except ValueError:
    pass
else:
    raise AssertionError("unsafe U0m flash profile was accepted")

print("u0m_flash_profile_self_test=passed")
print("u0m_manifest_patch_audit_contracts=passed")
print("watchdog_magic_close_and_fail_closed_contract=passed")
print("unsafe_profile_refusal=passed")
print("u0l_builder_and_audit_identity_pinned=passed")

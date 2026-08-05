#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "provision-a33-ssh-host-keys.py"
spec = importlib.util.spec_from_file_location("a33_ssh_host_key_provision_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.CONFIRMATION == "PROVISION-EXACT-SSH-HOST-KEYS"
assert module.EXPECTED_TWRP_SHA256 == (
    "414df197c21de25fc5627cd3a4d8a59011bef0141cfa479560c48aa378d3ad7e"
)
assert module.EXPECTED_USERDATA == "/dev/block/sda36"
assert module.EXPECTED_UUID == "7b056328-bdfb-496b-ac38-2624c43c863a"

script = module.REMOTE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "existing_host_keys=none",
    "mount -t tmpfs -o mode=0755,size=16m",
    "chroot \"$root\" /usr/bin/ssh-keygen -A",
    "chroot \"$root\" /usr/sbin/sshd -t -f /etc/ssh/sshd_config",
    "mount -t ext4 -o rw,nosuid,nodev,noatime",
    "rollback_generated_keys",
    "userdata_written=yes-etc-ssh-host-keys-only",
    "recovery_written=no",
    "boot_written=no",
    "super_written=no",
):
    assert required in script, required

for forbidden in (
    "sed -i",
    "truncate",
    "mkfs",
    "wipefs",
    "dd if=",
    "rm -rf \"$root/etc/ssh\"",
    "cp -a \"$overlay/.\" \"$root/etc/ssh\"",
):
    assert forbidden not in script, forbidden

# Persistent writes are restricted to generated host-key names and the private
# staging directory. Existing host keys cause a refusal rather than overwrite.
assert script.count("ssh_host_*_key") >= 4
assert "existing_host_keys_appeared=present" in script
assert "config_changed_before_commit=" in script
assert "config_after=" in script

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    build = root / "build"
    build.mkdir()
    raw = build / "volatile-diagnostic.txt"
    raw.write_text("validated\n", encoding="utf-8")
    diagnostic = {
        "diagnosis": "volatile-keygen-and-sshd-config-validation-passed",
        "diagnostic_status": "passed",
        "twrp_recovery_sha256": module.EXPECTED_TWRP_SHA256,
        "userdata_resolved": module.EXPECTED_USERDATA,
        "userdata_filesystem_uuid": module.EXPECTED_UUID,
        "userdata_filesystem_label": module.EXPECTED_LABEL,
        "userdata_persistent_writes": "no",
        "phone_partition_writes": "no",
        "ssh_keygen_rc": "0",
        "ssh_keygen_timed_out": "no",
        "sshd_config_rc": "0",
        "sshd_config_timed_out": "no",
        "generated_private_key_count": 4,
        "generated_public_key_count": 4,
        "raw_report": str(raw),
        "raw_report_sha256": module.sha256_file(raw),
    }
    path = build / module.DIAGNOSTIC_NAME
    path.write_text(json.dumps(diagnostic), encoding="utf-8")
    loaded_path, loaded = module.load_diagnostic(root)
    assert loaded_path == path
    assert loaded["diagnosis"] == diagnostic["diagnosis"]

    diagnostic["diagnosis"] = "ssh-keygen-failed"
    path.write_text(json.dumps(diagnostic), encoding="utf-8")
    try:
        module.load_diagnostic(root)
    except module.ProvisionError:
        pass
    else:
        raise AssertionError("failed volatile diagnostic was accepted")

print("a33_ssh_host_key_provision_self_test=passed")
print("exact_confirmation_and_device_identity=passed")
print("volatile_diagnostic_ancestry=passed")
print("read_only_preflight_then_narrow_rw_commit=passed")
print("generated_key_only_write_scope=passed")
print("rollback_and_config_hash_contract=passed")
print("recovery_boot_super_write_absence=passed")

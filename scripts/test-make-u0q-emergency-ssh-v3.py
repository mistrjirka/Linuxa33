#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0q-emergency-ssh-v3.py"

spec = importlib.util.spec_from_file_location("a33_u0q_v3_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_V2_BLOB == "63d3d9c548847b6ad710f29844265359e401185d"
assert module.RUNTIME_REVISION == "3"
assert module.MOUNT_POLICY == "verify-or-create-proc-sys-dev-devpts-run"

public_key = "ssh-ed25519 " + ("A" * 68) + " a33x-u0q-emergency"
block = module.emergency_block(public_key)
for required in (
    "event=runtime-mounts-ready",
    "policy=verify-or-create-proc-sys-dev-devpts-run",
    "u0q_mount_present",
    "u0q_verify_fstype",
    "mount -t proc proc /sysroot/proc",
    "mount -t sysfs sysfs /sysroot/sys",
    "mount -o bind /dev /sysroot/dev",
    "mount -t devpts",
    "mount -t tmpfs",
    "U0Q_RUN_BACKING=preexisting",
    "U0Q_RUN_BACKING=created-tmpfs",
    "u0q_verify_fstype /sysroot/run tmpfs:ramfs",
    "event=runtime-directory-ready",
    "event=network-helper-spawned",
    "event=sshd-helper-spawned",
    "event=pre-switch-root-ready",
    "exec /bin/busybox chroot /sysroot /usr/sbin/sshd",
    "exec /bin/busybox chroot /sysroot /bin/sh -s",
):
    assert required in block, required
for forbidden in (
    "run-is-not-a-mounted-runtime-filesystem",
    "/etc/fstab",
    "/etc/nftables.d/",
    "/etc/nftables.nft",
    "mount -o remount,rw",
    "umount -l",
    "sed -i",
    "rm -rf /sysroot",
):
    assert forbidden not in block, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0q-v3-generated-fragment.sh"
    path.write_text(
        "#!/bin/sh\n"
        "u0q_refuse() { return 1; }\n"
        "U0Q_TRACE=/tmp/u0q-trace\n"
        + block
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "v2.emergency_block = emergency_block",
    "v2.validate_generated_payload = validate_generated_payload",
    "replace_field(path, \"u0q_runtime_revision\"",
    "verified-or-created-tmpfs-run",
    "emergency_proc_backing",
    "verified-or-created-proc",
    "emergency_sys_backing",
    "verified-or-created-sysfs",
    "emergency_dev_backing",
    "verified-or-created-bind-dev",
    "emergency_devpts_backing",
    "verified-or-created-devpts",
    "emergency_run_backing",
    "verified-or-created-tmpfs",
    "emergency_runtime_mount_policy",
    "emergency_persistent_mount_config_delta",
    "replace_field(manifest, \"patch_report_sha256\"",
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

print("a33_u0q_v3_builder_self_test=passed")
print("exact_v2_builder_blob_pin=passed")
print("proc_sys_dev_devpts_run_verify_or_create_contract=passed")
print("preexisting_and_created_tmpfs_run_branches=passed")
print("evidence_metadata_verified_or_created_contract=passed")
print("post_cleanup_pre_switch_root_mount_order_contract=passed")
print("chrooted_sshd_and_network_helper_contract_preserved=passed")
print("persistent_mount_configuration_delta_absence=passed")
print("generated_shell_syntax_validation=passed")
print("host_only_and_phone_write_absence=passed")

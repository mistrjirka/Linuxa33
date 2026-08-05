#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "diagnose-a33-installed-sshd-variants.py"
spec = importlib.util.spec_from_file_location("a33_sshd_variants_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = """
selection_begin
sshd_disable_krb5=no
sshd_disable_pam=no
selected_candidate=/usr/sbin/sshd.pam
selection_end
variant_begin=sshd.krb5
variant_state=missing
variant_end=sshd.krb5
variant_begin=sshd.pam
variant_state=present
variant_path=/usr/sbin/sshd.pam
variant_sha256=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
variant_bytes=100
variant_mode=755
variant_config_rc=0
variant_config_timed_out=no
variant_config_elapsed_seconds=1
variant_config_output_begin
warning
variant_config_output_end
variant_listener_rc=0
variant_listener_survived_5s=yes
variant_listener_elapsed_seconds=5
variant_listener_log_says_listening=yes
variant_listener_output_begin
Server listening on 127.0.0.1 port 2222.
variant_listener_output_end
variant_end=sshd.pam
variant_begin=sshd
variant_state=present
variant_path=/usr/sbin/sshd
variant_config_rc=0
variant_config_timed_out=no
variant_listener_survived_5s=no
variant_listener_log_says_listening=no
variant_end=sshd
"""
selection = module.section(fixture, "selection_begin", "selection_end")
assert module.value(selection, "selected_candidate") == "/usr/sbin/sshd.pam"
variants = module.parse_variants(fixture)
assert len(variants) == 3
pam = next(item for item in variants if item["label"] == "sshd.pam")
assert pam["listener_survived_5s"] == "yes"
assert pam["listener_log_says_listening"] == "yes"
assert module.diagnose("/usr/sbin/sshd.pam", variants) == (
    "selected-sshd-listens-manually-openrc-startup-path-failed"
)

failed = [dict(item) for item in variants]
failed_pam = next(item for item in failed if item["label"] == "sshd.pam")
failed_pam["config_rc"] = "255"
assert module.diagnose("/usr/sbin/sshd.pam", failed) == (
    "openrc-selected-sshd-config-test-failed"
)
failed_pam["config_rc"] = "0"
failed_pam["listener_survived_5s"] = "no"
failed_pam["listener_log_says_listening"] = "no"
assert module.diagnose("/usr/sbin/sshd.pam", failed) == (
    "openrc-selected-sshd-exits-before-listening"
)
assert module.diagnose("/usr/sbin/sshd.missing", variants) == (
    "openrc-selected-sshd-variant-missing"
)

script = module.REMOTE_SCRIPT
for required in (
    "mount -t ext4 -o ro,noload,nosuid,nodev,noatime",
    "mount -t tmpfs -o mode=0755,size=4m",
    "selected_candidate=",
    "/usr/sbin/sshd.krb5 /usr/sbin/sshd.pam /usr/sbin/sshd",
    "-D -e -ddd",
    "-p 2222",
    "ListenAddress=127.0.0.1",
    "userdata_persistent_writes=no",
    "phone_partition_writes=no",
    "phone_reboot_performed=no",
):
    assert required in script
for forbidden in (
    "mount -t ext4 -o rw",
    "mount -o remount,rw",
    "dd if=",
    "mkfs",
    "wipefs",
    "sed -i",
    "adb reboot",
    "odin4",
):
    assert forbidden not in script

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "remote.sh"
    path.write_text(script, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

print("a33_installed_sshd_variant_diagnosis_self_test=passed")
print("openrc_selected_variant_parser=passed")
print("config_failure_classification=passed")
print("manual_listener_classification=passed")
print("read_only_rootfs_contract=passed")
print("volatile_listener_only_contract=passed")
print("phone_write_and_reboot_absence=passed")
print("shell_syntax_validation=passed")

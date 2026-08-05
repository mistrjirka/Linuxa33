#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0o-persistent-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0o_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0N_BUILDER_BLOB == "9b72b0ee3252f90d33f2cb6000210edfd35dd9cd"
assert module.EXPECTED_U0N_AUDIT_BLOB == "3152f2bbd504f842acd809156177b3c45cb7f800"
assert module.EXPECTED_U0N_INITRAMFS_SHA256 == "d0b4b75be3a7cadde1708a5e891001ec2b453e773068436ef645fff080631ef9"
assert module.EXPECTED_U0N_CANDIDATE_SHA256 == "9196109cba6a6e13f314b2aba28de21580c8b434c74e075c451d84b48da1bc2d"
assert module.TRACE_PATH == "/var/log/a33x-u0o-real-boot-sshd.log"

fixture = f'''#!/bin/sh
{module.SETUP_PREFIX}
u0n_refuse()
{{
{module.ORIGINAL_REFUSE_LINE.rstrip()}
    while true; do sleep 3600; done
}}
printf '<6>a33x-u0n-real-boot-sshd: stage=setup-success original=%s instrumented=%s\\n' "$u0n_original_sha" "$u0n_target_sha" > /dev/kmsg 2>/dev/null || true
if true; then
    if true; then
        if true; then
            printf '<6>a33x-u0n-real-boot-sshd: stage=splash-attempted method=show_splash\\n' > /dev/kmsg 2>/dev/null || true
        elif false; then
            printf '<6>a33x-u0n-real-boot-sshd: stage=splash-attempted method=fbsplash\\n' > /dev/kmsg 2>/dev/null || true
        else
            printf '<4>a33x-u0n-real-boot-sshd: stage=splash-unavailable\\n' > /dev/kmsg 2>/dev/null || true
        fi
    fi
fi
printf '<6>a33x-u0n-real-boot-sshd: stage=switch-root-ready\\n' > /dev/kmsg 2>/dev/null || true
cat >/tmp/embedded <<'EOF_EMBEDDED'
{module.ORIGINAL_KMSG.rstrip()}
u0n_monitor_body()
{{
    u0n_kmsg 6 "event=monitor-complete schedule=0,1,2,5,10,20,30,60"
}}
u0n_start_monitor_once()
{{
    u0n_kmsg 6 "event=monitor-started pid=$$ schedule=0,1,2,5,10,20,30,60"
}}
EOF_EMBEDDED
'''
old_hash = module.EXPECTED_U0N_INIT2_SHA256
module.EXPECTED_U0N_INIT2_SHA256 = module.v2.sha_bytes(fixture.encode())
try:
    patched = module.patch_init_second(fixture)
finally:
    module.EXPECTED_U0N_INIT2_SHA256 = old_hash

assert patched.count(module.TRACE_PATH) == 3
assert patched.count("candidate=U0o-persistent-sshd-trace") == 1
assert patched.count("source=initramfs") == 1
assert patched.count("source=openrc") == 1
assert patched.count('u0o_pre_trace 3 "error=$1"') == 1
assert patched.count('event=monitor-complete') == 1
assert patched.count('schedule=0,1,2,5,10,20,30,60') == 2
assert patched.count(': > "$U0O_TRACE"') == 1
for forbidden in (
    'rm -rf "/sysroot"',
    "mount -o remount,rw /sysroot",
    "> /sysroot/etc/",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in patched, forbidden

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0o-init.sh"
    path.write_text(patched, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "validate_parent(root, repo)",
    "assert_only_init_changed(before, after)",
    "before.one(WATCHDOG_TARGET).data != after.one(WATCHDOG_TARGET).data",
    "persistent_trace_write_scope",
    "truncate-on-u0o-boot-and-append-u0n-events-only",
    "rootfs_persistent_delta",
    "phone_partition_writes",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4",
):
    assert forbidden not in source, forbidden

print("a33_u0o_builder_self_test=passed")
print("exact_u0n_parent_blob_and_artifact_pins=passed")
print("init_2nd_only_payload_delta_contract=passed")
print("u0n_watchdog_hook_identity_contract=passed")
print("one_file_persistent_write_scope_contract=passed")
print("openrc_behavior_preservation_contract=passed")
print("host_only_and_phone_write_absence=passed")
print("shell_syntax_validation=passed")

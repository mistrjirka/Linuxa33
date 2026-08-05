#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0n-real-boot-sshd-trace-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0n_v2_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "9b72b0ee3252f90d33f2cb6000210edfd35dd9cd"
assert module.base.SNAPSHOT_SCHEDULE == (0, 1, 2, 5, 10, 20, 30, 60)
assert module.base.instrument_sshd_init is module.instrument_sshd_init

fixture = """#!/sbin/openrc-run
command=/usr/sbin/sshd
command_args=
pidfile=/run/sshd.pid

update_command() {
    command=/usr/sbin/sshd.pam
}

checkconfig() {
    update_command
    "$command" -t $command_args
}

start_pre() {
    checkconfig
}
"""
old_expected = module.base.EXPECTED_SSHD_INIT_SHA256
module.base.EXPECTED_SSHD_INIT_SHA256 = module.base.v2.sha_bytes(fixture.encode())
try:
    instrumented = module.instrument_sshd_init(fixture)
finally:
    module.base.EXPECTED_SSHD_INIT_SHA256 = old_expected

assert instrumented.count("event=monitor-started") == 1
assert instrumented.count("event=monitor-complete") == 1
assert instrumented.count("schedule=0,1,2,5,10,20,30,60") == 2
assert instrumented.count("\nupdate_command()\n") == 1
assert instrumented.count("\ncheckconfig()\n") == 1
assert instrumented.count("\nstart_pre()\n") == 1
assert "default_start" not in instrumented[len(fixture):]
assert "default_stop" not in instrumented[len(fixture):]

base_init = (
    "#!/bin/sh\n"
    + module.base.u0m_core.HANDOFF_BLOCK
    + "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
    + 'exec switch_root /sysroot "$init"\n'
)
patched_init = module.base.patch_init_second(base_init, instrumented)

with tempfile.TemporaryDirectory() as temporary:
    temp = Path(temporary)
    sshd = temp / "sshd.initd"
    init = temp / "init_2nd.sh"
    sshd.write_text(instrumented, encoding="utf-8")
    init.write_text(patched_init, encoding="utf-8")
    subprocess.run(["sh", "-n", str(sshd)], check=True)
    subprocess.run(["sh", "-n", str(init)], check=True)

source = MODULE.read_text(encoding="utf-8")
for forbidden in (
    'rm -rf "/sysroot"',
    "mount -o remount,rw /sysroot",
    "sed -i /sysroot",
    "adb reboot",
    "dd if=",
    "mkfs",
    "wipefs",
):
    assert forbidden not in source, forbidden

print("a33_u0n_v2_builder_self_test=passed")
print("exact_base_builder_blob_pin=passed")
print("monitor_start_and_completion_markers=passed")
print("two_schedule_occurrence_contract=passed")
print("openrc_default_start_stop_semantics_preserved=passed")
print("runtime_bind_and_shell_syntax_contract=passed")
print("rootfs_persistent_mutation_absence=passed")

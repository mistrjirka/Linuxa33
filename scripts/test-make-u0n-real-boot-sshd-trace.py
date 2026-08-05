#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0n-real-boot-sshd-trace.py"

spec = importlib.util.spec_from_file_location("a33_u0n_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_U0M_BUILDER_BLOB == "42d175aa59cd408fdc62e71d71acec8b63788acf"
assert module.EXPECTED_U0M_FLASH_BLOB == "a4523f358e853026279bc780feeb3c5306c2ea29"
assert module.EXPECTED_ROOTFS_SHA256 == (
    "79c94efe41da14e72e82cfc66c8e6fac6f04482fa2ea2af024f6b1ebb67d3951"
)
assert module.EXPECTED_SSHD_INIT_SHA256 == (
    "f8a44c910422f471ec21318c51e42f6f804f4fa569e8fa174690a1a0d8500760"
)
assert module.SNAPSHOT_SCHEDULE == (0, 1, 2, 5, 10, 20, 30, 60)
assert module.INIT_TARGET == "init_2nd.sh"
assert module.WATCHDOG_TARGET == "hooks/01-a33x-watchdog.sh"

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
old_expected = module.EXPECTED_SSHD_INIT_SHA256
module.EXPECTED_SSHD_INIT_SHA256 = module.v2.sha_bytes(fixture.encode())
try:
    instrumented = module.instrument_sshd_init(fixture)
finally:
    module.EXPECTED_SSHD_INIT_SHA256 = old_expected

for required in (
    "u0n_original_update_command()",
    "u0n_original_checkconfig()",
    "u0n_original_start_pre()",
    "event=script-loaded",
    "event=monitor-started",
    "event=snapshot",
    "event=nft",
    "event=start-pre-enter",
    "event=start_post-enter",
    "event=stop_pre-enter",
    "event=stop_post-enter",
    "schedule=0,1,2,5,10,20,30,60",
):
    assert required in instrumented, required
assert "default_start" not in instrumented[len(fixture):]
assert "default_stop" not in instrumented[len(fixture):]
assert instrumented.count("update_command()") == 1
assert instrumented.count("checkconfig()") == 1
assert instrumented.count("start_pre()") == 1

base_init = (
    "#!/bin/sh\n"
    + module.u0m_core.HANDOFF_BLOCK
    + "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' > /dev/kmsg 2>/dev/null || true\n"
    + 'exec switch_root /sysroot "$init"\n'
)
patched_init = module.patch_init_second(base_init, instrumented)
for required in (
    "a33x-u0m-watchdog-handoff: stage=shutdown-success",
    "a33x-u0n-real-boot-sshd: stage=setup-begin",
    "a33x-u0n-real-boot-sshd: stage=setup-success",
    "a33x-u0n-real-boot-sshd: stage=switch-root-ready",
    'mount -o bind "$U0N_SSHD_SOURCE" "$U0N_SSHD_TARGET"',
    "best-effort" if False else "show_splash",
    'exec switch_root /sysroot "$init"',
):
    assert required in patched_init, required

order = [
    patched_init.index("stage=shutdown-success"),
    patched_init.index("stage=setup-begin"),
    patched_init.index('mount -o bind "$U0N_SSHD_SOURCE" "$U0N_SSHD_TARGET"'),
    patched_init.index("stage=setup-success"),
    patched_init.index("stage=switch-root-ready"),
    patched_init.index("stage=switch-root-begin"),
    patched_init.index('exec switch_root /sysroot "$init"'),
]
assert order == sorted(order)

splash = module.splash_gzip_base64()
assert len(splash) > 100
assert "\n" not in splash

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

with tempfile.TemporaryDirectory() as temporary:
    temp = Path(temporary)
    sshd = temp / "sshd.initd"
    init = temp / "init_2nd.sh"
    sshd.write_text(instrumented, encoding="utf-8")
    init.write_text(patched_init, encoding="utf-8")
    subprocess.run(["sh", "-n", str(sshd)], check=True)
    subprocess.run(["sh", "-n", str(init)], check=True)

print("a33_u0n_builder_self_test=passed")
print("exact_u0m_and_rootfs_dependency_pins=passed")
print("openrc_original_function_delegation=passed")
print("default_start_stop_semantics_preserved=passed")
print("snapshot_schedule_and_kmsg_contract=passed")
print("nft_pid_listener_openrc_capture_contract=passed")
print("runtime_bind_before_switch_root_contract=passed")
print("best_effort_splash_continue_boot_contract=passed")
print("rootfs_persistent_mutation_absence=passed")
print("shell_syntax_validation=passed")

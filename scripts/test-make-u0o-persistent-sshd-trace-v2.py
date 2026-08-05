#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0o-persistent-sshd-trace-v2.py"

spec = importlib.util.spec_from_file_location("a33_u0o_v2_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

assert module.EXPECTED_BASE_BLOB == "56bee8bbf637fea7d0a077e1be2aed460dc85b7e"
assert module.base.patch_init_second is module.patch_init_second

fixture = f'''#!/bin/sh
{module.base.SETUP_PREFIX}
u0n_refuse()
{{
{module.base.ORIGINAL_REFUSE_LINE.rstrip()}
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
{module.base.ORIGINAL_KMSG.rstrip()}
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
old_hash = module.base.EXPECTED_U0N_INIT2_SHA256
module.base.EXPECTED_U0N_INIT2_SHA256 = module.base.v2.sha_bytes(fixture.encode())
try:
    patched = module.patch_init_second(fixture)
finally:
    module.base.EXPECTED_U0N_INIT2_SHA256 = old_hash

assert patched.count("event=monitor-complete*") == 1
assert patched.count("event=monitor-complete schedule=0,1,2,5,10,20,30,60") == 1
assert patched.count("schedule=0,1,2,5,10,20,30,60") == 2
assert patched.count(module.base.TRACE_PATH) == 3
assert patched.count(': > "$U0O_TRACE"') == 1

with tempfile.TemporaryDirectory() as temporary:
    path = Path(temporary) / "u0o-v2-init.sh"
    path.write_text(patched, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

source = MODULE.read_text(encoding="utf-8")
for required in (
    "base.patch_init_second = patch_init_second",
    "event=monitor-complete*",
    "event=monitor-complete schedule=0,1,2,5,10,20,30,60",
    "return base.main()",
):
    assert required in source, required
for forbidden in (
    "adb reboot",
    "fastboot",
    "odin4",
):
    assert forbidden not in source, forbidden

print("a33_u0o_v2_builder_self_test=passed")
print("exact_base_builder_blob_pin=passed")
print("monitor_event_and_sync_policy_roles_distinguished=passed")
print("one_file_persistent_trace_contract=passed")
print("host_only_and_phone_write_absence=passed")
print("shell_syntax_validation=passed")

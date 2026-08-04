#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0k-direct-mount-isolation.py"
spec = importlib.util.spec_from_file_location("a33_u0k_direct_mount", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = f'''#!/bin/busybox ash
{module.ORIGINAL_HANDOFF_BLOCK}
init="/sbin/init"
setup_bootchart2

{module.ORIGINAL_SWITCH_BLOCK}
exec switch_root /sysroot "$init"
'''
patched = module.patch_second_stage(fixture)

for command in module.SKIPPED_CALLS:
    assert not module.executable_calls(patched, command)
assert module.executable_calls(patched, "wait_root_partition") == ["wait_root_partition"]
assert module.executable_calls(patched, "mount_root_partition") == ["mount_root_partition"]
assert module.executable_calls(patched, "resize_filesystem_after_mount") == [
    "resize_filesystem_after_mount /sysroot"
]
for marker in module.MARKERS:
    assert patched.count(f"{module.MARKER_PREFIX}: stage={marker}") == 1
assert patched.count('exec switch_root /sysroot "$init"') == 1

with tempfile.TemporaryDirectory() as temp:
    script = Path(temp) / "init_2nd.sh"
    script.write_text(patched, encoding="utf-8")
    subprocess.run(["sh", "-n", str(script)], check=True)

try:
    module.patch_second_stage(fixture.replace("resize_root_filesystem\n", ""))
except module.Refusal:
    pass
else:
    raise AssertionError("modified U0j handoff block was accepted")

print("u0k_direct_mount_self_test=passed")
print("exact_handoff_block_contract=passed")
print("resize_and_legacy_boot_calls_removed=passed")
print("mount_root_retained=passed")
print("switch_root_retained=passed")
print("stage_marker_order=passed")
print("modified_base_refusal=passed")

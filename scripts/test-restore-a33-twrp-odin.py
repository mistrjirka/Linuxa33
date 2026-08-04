#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
MODULE = HERE / "restore-a33-twrp-odin.py"
spec = importlib.util.spec_from_file_location("a33_twrp_restore_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

module.require_confirmation(module.REQUIRED_CONFIRMATION)
for bad in ("", "restore-exact-twrp", "RESTORE-TWRP"):
    try:
        module.require_confirmation(bad)
    except module.RestoreError:
        pass
    else:
        raise AssertionError(f"unsafe confirmation token was accepted: {bad!r}")

assets = module.verify.RescueAssets(
    odin=Path("/tmp/odin4"),
    odin_sha256="1" * 64,
    rescue_tar=Path("/tmp/twrp.tar"),
    rescue_tar_sha256="2" * 64,
    twrp_size=100663296,
    twrp_sha256="3" * 64,
    report=Path("/tmp/report.txt"),
)
pairs = dict(module.report_pairs(assets))
assert pairs["operation"] == "restore-exact-twrp-through-odin-python"
assert pairs["implementation_language"] == "python3"
assert pairs["userdata_written"] == "no"
assert pairs["cache_written"] == "no"
assert pairs["super_written"] == "no"
assert pairs["boot_written"] == "no"
assert pairs["recovery_written"] == "yes"
assert pairs["reboot_performed"] == "no"
assert pairs["next_action"] == "boot-twrp-directly-before-android"

print("a33_twrp_odin_restore_self_test=passed")
print("explicit_confirmation_contract=passed")
print("recovery_only_write_contract=passed")
print("no_automatic_reboot_contract=passed")

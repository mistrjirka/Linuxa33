#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0m-watchdog-magic-close-v3.py"
spec = importlib.util.spec_from_file_location("a33_u0m_v3_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

fixture = "#!/bin/sh\n" + module.base.ORIGINAL_FEEDER_BLOCK
patched = module.patch_watchdog_hook(fixture)
assert patched.count("printf 'V' >&3") == 1
assert patched.count("exec 3>&-") == 1
assert patched.count(module.base.STOP_LOG) == 2
assert patched.count(module.base.DID_NOT_STOP_LOG) == 2
assert "config_nowayout=disabled" in patched
assert "driver stop log verified" in patched
assert "failed-unverified-stop" in patched
assert "/sys/class/watchdog/watchdog0/state" not in patched
assert "/sys/class/watchdog/watchdog0/nowayout" not in patched
assert "/sys/module/s3c2410_wdt/parameters/nowayout" not in patched
assert "read_watchdog_nowayout" not in patched

with tempfile.TemporaryDirectory() as temp:
    path = Path(temp) / "watchdog.sh"
    path.write_text(patched, encoding="utf-8")
    subprocess.run(["sh", "-n", str(path)], check=True)

for bad in (
    fixture.replace(module.base.ORIGINAL_FEEDER_BLOCK, ""),
    fixture + module.base.ORIGINAL_FEEDER_BLOCK,
    fixture.replace("#!/bin/sh\n", "#!/bin/sh\nWATCHDOG_SHUTDOWN_REQUEST=x\n"),
):
    try:
        module.patch_watchdog_hook(bad)
    except module.base.Refusal:
        pass
    else:
        raise AssertionError("unsafe U0m v3 watchdog fixture was accepted")

assert module.EXPECTED_BASE_BLOB == "19cb63ea55ecfb7a186016058b7303b4326c9030"
assert module.EXPECTED_INSPECTOR_BLOB == "ea17562fba369bba3da81c291e22a15c663c929d"
print("a33_u0m_v3_builder_self_test=passed")
print("host_config_nowayout_contract=passed")
print("runtime_parameter_not_required=passed")
print("driver_stop_log_verification=passed")
print("failed_stop_reopens_and_feeds=passed")
print("magic_close_and_fd_close_contract=passed")
print("shell_syntax_validation=passed")
print("base_and_inspector_identity_pinned=passed")

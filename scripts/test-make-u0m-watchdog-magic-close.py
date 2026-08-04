#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
MODULE = HERE / "make-u0m-watchdog-magic-close.py"
spec = importlib.util.spec_from_file_location("a33_u0m_builder_test", MODULE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

watchdog_fixture = "#!/bin/sh\n" + module.ORIGINAL_FEEDER_BLOCK
patched_watchdog = module.patch_watchdog_hook(watchdog_fixture)
assert patched_watchdog.startswith("#!/bin/sh\n")
assert patched_watchdog.count("printf 'V' >&3") == 1
assert patched_watchdog.count("exec 3>&-") == 1
assert patched_watchdog.count("watchdog0/nowayout") == 1
assert patched_watchdog.count("watchdog0/state") == 2
assert patched_watchdog.count('"stopped" > "$WATCHDOG_SHUTDOWN_STATUS"') == 1
assert "sleep 8" not in patched_watchdog
assert "sleep 1" in patched_watchdog
assert "continue-feeding" not in patched_watchdog

switch_marker = (
    "printf '<6>a33x-u0k-direct-mount: stage=switch-root-begin\\n' "
    "> /dev/kmsg 2>/dev/null || true\n"
)
init_fixture = (
    "#!/bin/sh\n"
    + module.INIT_ANCHOR
    + switch_marker
    + 'exec switch_root /sysroot "$init"\n'
)
patched_init = module.patch_init_second(init_fixture)
assert patched_init.count("stage=shutdown-request") == 1
assert patched_init.count("stage=shutdown-success") == 1
assert patched_init.count("shutdown-failed") == 1
assert patched_init.count("watchdog0/state") == 1
assert patched_init.index("stage=shutdown-success") < patched_init.index(
    "stage=switch-root-begin"
)
assert patched_init.index("stage=switch-root-begin") < patched_init.index(
    "exec switch_root"
)

with tempfile.TemporaryDirectory() as temp:
    root = Path(temp)
    hook_path = root / "watchdog.sh"
    init_path = root / "init_2nd.sh"
    hook_path.write_text(patched_watchdog, encoding="utf-8")
    init_path.write_text(patched_init, encoding="utf-8")
    subprocess.run(["sh", "-n", str(hook_path)], check=True)
    subprocess.run(["sh", "-n", str(init_path)], check=True)

for bad in (
    watchdog_fixture.replace(module.ORIGINAL_FEEDER_BLOCK, ""),
    watchdog_fixture + module.ORIGINAL_FEEDER_BLOCK,
    watchdog_fixture.replace("#!/bin/sh\n", "#!/bin/sh\nWATCHDOG_SHUTDOWN_REQUEST=x\n"),
):
    try:
        module.patch_watchdog_hook(bad)
    except module.Refusal:
        pass
    else:
        raise AssertionError("unsafe U0m watchdog fixture was accepted")

for bad in (
    init_fixture.replace(module.INIT_ANCHOR, ""),
    init_fixture.replace(module.INIT_ANCHOR, module.INIT_ANCHOR * 2),
    init_fixture.replace("#!/bin/sh\n", f"#!/bin/sh\n# {module.MARKER_PREFIX}\n"),
):
    try:
        module.patch_init_second(bad)
    except module.Refusal:
        pass
    else:
        raise AssertionError("unsafe U0m init fixture was accepted")

assert module.EXPECTED_U0L_BUILDER_BLOB == (
    "6c3133d5efbbdf08c3197eae3693d215fbf1b642"
)
assert module.EXPECTED_U0L_FLASH_BLOB == (
    "0c8ed99e7d1e75b42cf54921f7f217cad6c4f845"
)
assert module.EXPECTED_WATCHDOG_SOURCE_BLOB == (
    "ed779bb8ee90a9f64438a679923a852829bc5fb0"
)
assert module.MARKERS == ("shutdown-request", "shutdown-success")

print("a33_u0m_watchdog_magic_close_self_test=passed")
print("exact_u0l_watchdog_block_patch=passed")
print("magic_close_V_and_fd_close_contract=passed")
print("nowayout_and_state_preconditions=passed")
print("failed_shutdown_continues_feeding_contract=passed")
print("shutdown_before_switch_root_order=passed")
print("shell_syntax_validation=passed")
print("missing_duplicate_anchor_refusal=passed")
print("preexisting_handoff_refusal=passed")
print("u0l_and_watchdog_identity_pinned=passed")

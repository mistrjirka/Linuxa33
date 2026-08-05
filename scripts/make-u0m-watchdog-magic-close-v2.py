#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "make-u0m-watchdog-magic-close.py"

spec = importlib.util.spec_from_file_location("a33_u0m_builder_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0m builder: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def patch_watchdog_hook(text: str) -> str:
    if text.count(base.ORIGINAL_FEEDER_BLOCK) != 1:
        base.refuse("U0l watchdog feeder block does not match exactly once")
    if "WATCHDOG_SHUTDOWN_REQUEST" in text or base.MARKER_PREFIX in text:
        base.refuse("watchdog handoff logic already exists in base hook")
    patched = text.replace(base.ORIGINAL_FEEDER_BLOCK, base.REPLACEMENT_FEEDER_BLOCK)
    required_counts = (
        (f"WATCHDOG_NOWAYOUT_PARAMETER={base.NOWAYOUT_PARAMETER}", 1),
        ("read_watchdog_nowayout()", 1),
        ("N|n|0", 1),
        ("Y|y|1", 1),
        ("watchdog_log_count()", 1),
        (base.STOP_LOG, 2),
        (base.DID_NOT_STOP_LOG, 2),
        ("printf 'V' >&3", 1),
        ("exec 3>&-", 1),
        ('printf \'%s\\n\' "stopped" > "$WATCHDOG_SHUTDOWN_STATUS"', 1),
        ("driver stop log verified", 1),
        ("failed-unverified-stop", 1),
    )
    for token, expected in required_counts:
        actual = patched.count(token)
        if actual != expected:
            base.refuse(
                "patched watchdog hook contract is missing or duplicated: "
                f"token={token!r} actual={actual} expected={expected}"
            )
    return patched


base.patch_watchdog_hook = patch_watchdog_hook


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

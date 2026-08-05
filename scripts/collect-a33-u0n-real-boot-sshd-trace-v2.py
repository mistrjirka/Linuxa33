#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "collect-a33-u0n-real-boot-sshd-trace.py"
EXPECTED_BASE_BLOB = "060b98413c408326843eb1a61df9e7bcc06d5744"

spec = importlib.util.spec_from_file_location("a33_u0n_collector_v2_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0n collector: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

base.FOCUS_PATTERN = re.compile(
    r"a33x-u0n-real-boot-sshd|a33x-u0m-watchdog-handoff|a33x-u0l-openrc-cgroup-isolation|"
    r"a33x-u0k-direct-mount|a33x-watchdog-v2|sshd(?:\.pam)?|ssh-keygen|"
    r"openrc|start-stop-daemon|nft|dport\s+22|watchdog0|watchdog reset|"
    r"cl0_wdtreset|freqboost|\bems\b|cgroup(?:\.procs)?|switch_root|sysroot|"
    r"kernel panic|panic - not syncing|call trace|bug:|oops|unable to handle|"
    r"exynos_plist_add|exynos_pm_qos|exynos_ufs_probe|ext4-fs|dwc3|gadget",
    re.IGNORECASE,
)


def main() -> int:
    repo = Path.home() / "Linuxa33"
    actual = subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(BASE_PATH)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    if actual != EXPECTED_BASE_BLOB:
        raise base.U0nCollectError(
            f"checked-in U0n collector changed: actual={actual!r} expected={EXPECTED_BASE_BLOB!r}"
        )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.U0nCollectError,
        base.observer.U0nObserveError,
        base.flash.U0nFlashError,
        base.flash.restore.cleanup.CleanupV2Error,
        base.flash.recovery_helper.ExactRecoveryNodeError,
        base.common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(f"U0n TRACE COLLECTION V2 FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

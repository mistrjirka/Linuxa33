#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_PATH = HERE / "observe-a33-u0o-persistent-sshd-trace.py"
EXPECTED_BASE_BLOB = "952ce1d03b79f4cb4d29ad83600d2220be727e01"

spec = importlib.util.spec_from_file_location("a33_u0o_observer_v2_base", BASE_PATH)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0o observer: {BASE_PATH}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)


def adb_boot_id(adb: str, serial: str, timeout: float = 2.0) -> str:
    try:
        completed = base.common.run(
            [
                adb,
                "-s",
                serial,
                "shell",
                "cat",
                "/proc/sys/kernel/random/boot_id",
            ],
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.replace("\r", "").strip()


base.adb_boot_id = adb_boot_id


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
        raise base.U0oObserveError(
            f"checked-in U0o observer changed: actual={actual!r} "
            f"expected={EXPECTED_BASE_BLOB!r}"
        )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.U0oObserveError,
        base.flash.U0oFlashError,
        base.flash.u0n_flash_v2.U0nFlashV2Error,
        base.flash.base.U0nFlashError,
        base.flash.base.restore.RestoreError,
        base.flash.base.restore.cleanup.CleanupV2Error,
        base.flash.base.restore.block_helper.ExactBlockNodeError,
        base.flash.base.restore.identity_helper.Ext4IdentityError,
        base.flash.base.recovery_helper.ExactRecoveryNodeError,
        base.common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(f"U0o OBSERVER V2 FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

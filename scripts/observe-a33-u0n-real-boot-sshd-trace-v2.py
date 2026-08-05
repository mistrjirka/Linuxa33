#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_OBSERVER_PATH = HERE / "observe-a33-u0n-real-boot-sshd-trace.py"
FLASH_V2_PATH = HERE / "flash-a33-u0n-real-boot-sshd-trace-v2.py"
EXPECTED_BASE_OBSERVER_BLOB = "31b6f288ddeb743afb8b338b08c7169dbfe4f31e"
EXPECTED_FLASH_V2_BLOB = "337807470888e0d00a6afb40a5a7ce7bcd8875c3"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


flash_v2 = load("a33_u0n_observer_v2_flash", FLASH_V2_PATH)
base = load("a33_u0n_observer_v2_base", BASE_OBSERVER_PATH)

# Preserve the original observer implementation and its exact flash-report
# contract. Redirect only its imported flash module to the corrected, pinned
# shell-safe phone preflight implementation.
base.flash = flash_v2.base
base.common = flash_v2.base.common


def git_blob(repo: Path, path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    ).stdout.strip()


def main() -> int:
    repo = Path.home() / "Linuxa33"
    for path, expected in (
        (BASE_OBSERVER_PATH, EXPECTED_BASE_OBSERVER_BLOB),
        (FLASH_V2_PATH, EXPECTED_FLASH_V2_BLOB),
    ):
        actual = git_blob(repo, path)
        if actual != expected:
            raise base.U0nObserveError(
                f"checked-in U0n observer v2 dependency changed: "
                f"path={path.name} actual={actual!r} expected={expected!r}"
            )
    return base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        base.U0nObserveError,
        flash_v2.U0nFlashV2Error,
        base.flash.U0nFlashError,
        base.flash.restore.RestoreError,
        base.flash.restore.cleanup.CleanupV2Error,
        base.flash.restore.block_helper.ExactBlockNodeError,
        base.flash.restore.identity_helper.Ext4IdentityError,
        base.flash.recovery_helper.ExactRecoveryNodeError,
        base.common.Refusal,
        OSError,
        ValueError,
    ) as exc:
        print(f"U0n OBSERVER V2 FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

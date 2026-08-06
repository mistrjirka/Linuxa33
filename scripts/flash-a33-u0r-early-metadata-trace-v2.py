#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_FLASH_PATH = HERE / "flash-a33-u0r-early-metadata-trace.py"
BUILDER_V2_PATH = HERE / "make-u0r-early-metadata-trace-v2.py"
EXPECTED_BASE_FLASH_BLOB = "36923334243058f2f836b5cf3710b0c642bac2f8"
EXPECTED_BUILDER_V2_BLOB = "508ec2ea9b7b846342ec58bb1ea882ddd7041f60"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


base_flash = load("a33_u0r_flash_v2_base", BASE_FLASH_PATH)
builder_v2 = load("a33_u0r_flash_v2_builder", BUILDER_V2_PATH)


class U0rFlashV2Error(RuntimeError):
    pass


def git_blob(path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(HERE.parent), "hash-object", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return completed.stdout.strip()


def activate_corrected_builder() -> None:
    actual_flash = git_blob(BASE_FLASH_PATH)
    if actual_flash != EXPECTED_BASE_FLASH_BLOB:
        raise U0rFlashV2Error(
            f"base U0r flash changed: actual={actual_flash} expected={EXPECTED_BASE_FLASH_BLOB}"
        )
    actual_builder = git_blob(BUILDER_V2_PATH)
    if actual_builder != EXPECTED_BUILDER_V2_BLOB:
        raise U0rFlashV2Error(
            f"corrected U0r builder changed: actual={actual_builder} expected={EXPECTED_BUILDER_V2_BLOB}"
        )
    base_flash.BUILDER_PATH = BUILDER_V2_PATH
    base_flash.EXPECTED_BUILDER_BLOB = EXPECTED_BUILDER_V2_BLOB
    base_flash.builder = builder_v2


activate_corrected_builder()

# Explicit exports consumed by the observer and by exception handling.
CONFIRMATION = base_flash.CONFIRMATION
EXPECTED_CANDIDATE_SIZE = base_flash.EXPECTED_CANDIDATE_SIZE
REMOTE_CANDIDATE = base_flash.REMOTE_CANDIDATE
REPORT_NAME = base_flash.REPORT_NAME
U0rFlashError = base_flash.U0rFlashError
builder = builder_v2
parent = base_flash.parent
base = base_flash.base
common = base_flash.common
local_evidence = base_flash.local_evidence


def main() -> int:
    activate_corrected_builder()
    return base_flash.main()


def __getattr__(name: str):
    return getattr(base_flash, name)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0rFlashV2Error,
        builder_v2.U0rV2Error,
        base_flash.U0rFlashError,
        base_flash.parent.U0qV2FlashError,
        base_flash.parent.u0p_flash.U0pFlashError,
        base_flash.base.U0nFlashError,
        base_flash.common.Refusal,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        print(f"REFUSING U0r V2 FLASH: {exc}", file=sys.stderr)
        raise SystemExit(1)

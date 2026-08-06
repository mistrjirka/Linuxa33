#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BASE_OBSERVER_PATH = HERE / "observe-a33-u0r-early-metadata-trace.py"
FLASH_V2_PATH = HERE / "flash-a33-u0r-early-metadata-trace-v2.py"
EXPECTED_BASE_OBSERVER_BLOB = "8fbe86e7705f90d492fc4b13e05227d00c9f0b61"
EXPECTED_FLASH_V2_BLOB = "51662f7e2b289b850424fd32b324abade023e865"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


observer = load("a33_u0r_observer_v2_base", BASE_OBSERVER_PATH)
flash_v2 = load("a33_u0r_observer_v2_flash", FLASH_V2_PATH)


class U0rObserveV2Error(RuntimeError):
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


def activate_corrected_flash() -> None:
    actual_observer = git_blob(BASE_OBSERVER_PATH)
    if actual_observer != EXPECTED_BASE_OBSERVER_BLOB:
        raise U0rObserveV2Error(
            "base U0r observer changed: "
            f"actual={actual_observer} expected={EXPECTED_BASE_OBSERVER_BLOB}"
        )
    actual_flash = git_blob(FLASH_V2_PATH)
    if actual_flash != EXPECTED_FLASH_V2_BLOB:
        raise U0rObserveV2Error(
            f"corrected U0r flash changed: actual={actual_flash} expected={EXPECTED_FLASH_V2_BLOB}"
        )
    observer.FLASH_PATH = FLASH_V2_PATH
    observer.EXPECTED_FLASH_BLOB = EXPECTED_FLASH_V2_BLOB
    observer.flash = flash_v2


def main() -> int:
    activate_corrected_flash()
    return observer.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        U0rObserveV2Error,
        flash_v2.U0rFlashV2Error,
        flash_v2.U0rFlashError,
        observer.U0rObserveError,
        subprocess.SubprocessError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        print(f"U0r V2 OBSERVER FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "collect-a33-u0m-previous-boot.py"
FLASH = HERE / "flash-a33-u0m-watchdog-magic-close-v4.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_u0m_v4_collector_base", BASE)
flash = load("a33_u0m_v4_collector_flash", FLASH)

base.u0m = flash.base
base.PROFILE = base.ObserverProfile(
    expected_flash_operation="flash-exact-u0m-v3-watchdog-magic-close",
    flash_report_name="a33-first-rootfs-u0m-v3-watchdog-magic-close-flash.txt",
    output_prefix="u0m-v4-watchdog-magic-close-observation",
    observation_operation="observe-u0m-v4-watchdog-magic-close",
)
base.OUTPUT_PREFIX = "u0m-v4-watchdog-magic-close-result"


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

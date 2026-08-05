#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "collect-a33-u0m-previous-boot.py"
FLASH_V2 = HERE / "flash-a33-u0m-watchdog-magic-close-v2.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


base = load("a33_u0m_collector_v2_base", BASE)
flash_v2 = load("a33_u0m_collector_v2_flash", FLASH_V2)
base.u0m = flash_v2.base


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())

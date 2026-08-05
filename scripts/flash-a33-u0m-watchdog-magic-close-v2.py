#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
BASE = HERE / "flash-a33-u0m-watchdog-magic-close.py"
AUDIT_V2 = HERE / "audit-a33-u0m-candidate-v2.py"
EXPECTED_U0M_BUILDER_BLOB = "19cb63ea55ecfb7a186016058b7303b4326c9030"
EXPECTED_U0M_AUDIT_V2_BLOB = "80cee6825ea96ef18799ab46828d9f3fb0b566cd"

spec = importlib.util.spec_from_file_location("a33_u0m_flash_v2_base", BASE)
if spec is None or spec.loader is None:
    raise SystemExit(f"cannot load U0m flash path: {BASE}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

base.EXPECTED_U0M_BUILDER_BLOB = EXPECTED_U0M_BUILDER_BLOB
base.U0M_AUDIT = AUDIT_V2
base.EXPECTED_U0M_AUDIT_BLOB = EXPECTED_U0M_AUDIT_V2_BLOB


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
